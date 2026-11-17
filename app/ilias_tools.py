"""ILIAS diagnostic and download tools built on top of the existing downloader.

New CLI commands:
  inspect-url       Analyze an ILIAS URL without downloading
  discover-course   Export full course structure as JSON
  opencast-inspect  Show OpenCast event details and media URLs
  download-url      Auto-detect object type and download
  download-folder   Recursively download an ILIAS folder
  live-discovery    Derive recorder/room mappings from metadata
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Import from existing project modules
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))

from downloader import (
    BASE, KINDS, AUTH_MARKERS,
    launch_browser, object_id, goto, parse_items, session_expansions,
    safe_name, local_path, atomic_json, authenticated_html,
    download as download_file, ConnectionFailure, request_with_retry,
)
from sync_support import parse_date, week_folder, file_revision, DownloadIndex
from podcast_sync import player_data, events as podcast_events, best_mp4

HERE = Path(__file__).resolve().parent
if HERE.name == 'app':
    HERE = HERE.parent
STATE = HERE / ".auth" / "session.json"


# ===================================================================
#  inspect_url  —  Diagnose an ILIAS URL
# ===================================================================

def inspect_url(url, context=None, verbose=True):
    """Inspect an ILIAS URL: resolve type, ref_id, redirects, headers."""
    identity = object_id(url)
    result = {
        "input_url": url,
        "detected_type": identity[0] if identity else None,
        "detected_ref_id": identity[1] if identity else None,
        "redirects": [],
        "final_url": None,
        "content_type": None,
        "content_length": None,
        "content_disposition": None,
        "etag": None,
        "last_modified": None,
        "accept_ranges": None,
    }

    if context is not None:
        # Follow redirects and inspect headers via HEAD-like request
        try:
            resp = context.request.head(url, timeout=30000)
        except Exception:
            resp = context.request.get(url, timeout=30000)

        result["final_url"] = resp.url
        result["http_status"] = resp.status
        headers = resp.headers
        result["content_type"] = headers.get("content-type")
        result["content_length"] = headers.get("content-length")
        result["content_disposition"] = headers.get("content-disposition")
        result["etag"] = headers.get("etag")
        result["last_modified"] = headers.get("last-modified")
        result["accept_ranges"] = headers.get("accept-ranges")

        # Check if this is a file download
        if result["content_disposition"] and "filename" in result["content_disposition"]:
            from email.message import Message
            msg = Message()
            msg["content-disposition"] = result["content_disposition"]
            result["filename"] = msg.get_filename()

        resp.dispose()

    if verbose:
        for key, val in result.items():
            if val is not None:
                print(f"  {key}: {val}")

    return result


# ===================================================================
#  discover_course  —  Export full course structure as JSON
# ===================================================================

def discover_course(ref_id, context, max_pages=500, delay=0.25):
    """Traverse a course recursively and return its full structure as JSON."""
    url = goto("crs", ref_id)
    tree = {
        "ref_id": str(ref_id),
        "type": "course",
        "title": None,
        "children": [],
        "files": [],
        "podcasts": [],
    }
    queue = [("crs", str(ref_id), tree, url, None)]
    visited = set()
    last_request = 0.0

    while queue:
        kind, ref, parent_node, nav_url, session_day = queue.pop(0)
        query = parse_qs(urlsplit(nav_url).query)
        key = (kind, ref, str(session_day),
               tuple((name, tuple(query.get(name, [])))
                     for name in ('crs_next_sess', 'crs_prev_sess')))
        if key in visited:
            continue
        if len(visited) >= max_pages:
            break
        visited.add(key)

        time.sleep(max(0, delay - (time.monotonic() - last_request)))
        last_request = time.monotonic()

        try:
            response = context.request.get(nav_url, timeout=45000)
        except Exception as exc:
            print(f"  [FEHLER] Seite {ref}: {exc}", file=sys.stderr)
            continue

        if not response.ok:
            response.dispose()
            continue

        page_url = response.url
        html = response.text()
        response.dispose()

        soup = BeautifulSoup(html, "html.parser")
        if not authenticated_html(page_url, soup):
            raise RuntimeError("Session abgelaufen")

        # Extract title from page
        title_el = soup.select_one("h1.ilHeader, h1")
        if title_el and parent_node.get("title") is None:
            parent_node["title"] = title_el.get_text(" ", strip=True)

        items = parse_items(soup, page_url)
        reference_day = __import__("datetime").date.today()

        # Session expansion
        if kind == "crs":
            for expanded in session_expansions(soup, page_url, ref):
                queue.append((kind, ref, parent_node, expanded, session_day))

        for (child_kind, child_ref), (title, child_url) in items.items():
            if child_kind == "file":
                # Extract revision info
                revisions = {}
                for anchor in soup.select('.il_ContainerItemTitle a[href], .il-item-title a[href]'):
                    ident = object_id(urljoin(page_url, anchor['href']))
                    if ident and ident[0] == 'file':
                        row = anchor.find_parent('div', class_='ilContainerListItemOuter')
                        revisions[ident[1]] = file_revision(row, anchor.get_text(' ', strip=True), reference_day)

                parent_node["files"].append({
                    "ref_id": child_ref,
                    "title": title,
                    "download_url": f"{BASE}ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={child_ref}",
                    "revision": revisions.get(child_ref),
                })

            elif child_kind in ("fold", "grp"):
                child_node = {
                    "ref_id": child_ref,
                    "type": "folder" if child_kind == "fold" else "group",
                    "title": title,
                    "children": [],
                    "files": [],
                    "podcasts": [],
                }
                parent_node["children"].append(child_node)
                day = parse_date(title, reference_day) or session_day
                queue.append((child_kind, child_ref, child_node, child_url, day))

            elif child_kind == "info":
                day = parse_date(title, reference_day) or session_day
                queue.append((child_kind, child_ref, parent_node, child_url, day))

        # Detect OpenCast/podcast links in the page
        for anchor in soup.select("a[href]"):
            href = urljoin(page_url, anchor.get("href", ""))
            q = parse_qs(urlsplit(href).query)
            cmd_class = q.get("cmdClass", [""])[0].lower()
            if cmd_class in ("ilobjopencastgui", "xocteventgui"):
                podcast_ref = q.get("ref_id", [""])[0]
                if podcast_ref:
                    parent_node["podcasts"].append({
                        "ref_id": podcast_ref,
                        "title": anchor.get_text(" ", strip=True),
                        "url": href,
                    })

    return tree


# ===================================================================
#  opencast_inspect  —  Show OpenCast event details
# ===================================================================

def opencast_inspect(ref_or_eid, context):
    """Inspect an OpenCast plugin page or a specific event."""
    result = {"events": []}

    # Determine if this is a ref_id (numeric) or event_id (UUID)
    is_ref = ref_or_eid.isdigit()
    is_eid = bool(re.match(r'^[0-9a-f]{8}-', ref_or_eid))

    if is_ref:
        # Load the podcast list page
        list_url = (BASE + "ilias.php?baseClass=ilobjplugindispatchgui&cmdNode=qp:qi&"
                    f"cmdClass=ilObjOpenCastGUI&cmd=showContent&ref_id={ref_or_eid}")
        resp = context.request.get(list_url, timeout=60000)
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status} für Podcast-Liste {ref_or_eid}")
        html = resp.text()
        page_url = resp.url
        resp.dispose()

        found = podcast_events(html, page_url)
        result["ref_id"] = ref_or_eid
        result["event_count"] = len(found)

        for eid, ev in sorted(found.items(), key=lambda x: (x[1].get("date", ""), x[1].get("time", ""))):
            event_info = {"event_id": eid, **ev, "streams": []}
            try:
                player_resp = context.request.get(ev["url"], timeout=60000)
                if player_resp.ok:
                    pdata = player_data(player_resp.text())
                    for stream in pdata.get("streams", []):
                        stream_info = {"content": stream.get("content"), "sources": []}
                        for src in stream.get("sources", {}).get("mp4", []):
                            stream_info["sources"].append({
                                "url": src.get("src"),
                                "resolution": src.get("res"),
                                "mimetype": src.get("mimetype"),
                            })
                        event_info["streams"].append(stream_info)

                    # Extract metadata
                    event_info["metadata"] = pdata.get("metadata", {})
                    event_info["frame_count"] = len(pdata.get("frameList", []))
                    event_info["has_captions"] = len(pdata.get("captions", [])) > 0

                player_resp.dispose()
            except Exception as exc:
                event_info["error"] = str(exc)

            result["events"].append(event_info)

    elif is_eid:
        # We need a ref_id to reach the player page
        result["error"] = ("Event-ID ohne ref_id: Bitte ref_id des Podcast-Moduls angeben, "
                          "z.B. opencast-inspect 3762440 oder opencast-inspect 3762440:b57bcbbc-...")

    return result


# ===================================================================
#  download_url  —  Auto-detect and download any ILIAS URL
# ===================================================================

def download_url(url, output_dir, context, index=None):
    """Auto-detect object type from URL and download accordingly."""
    identity = object_id(url)
    if not identity:
        # Try to interpret as ref_id
        if url.strip().isdigit():
            identity = ("crs", url.strip())
        else:
            raise ValueError(f"URL nicht erkannt: {url}")

    kind, ref = identity
    root = Path(output_dir).resolve()

    if kind == "file":
        root.mkdir(parents=True, exist_ok=True)
        path, status = download_file(context, root, Path("."), ref, index=index)
        print(f"  {status}: {path}")
        return {"type": "file", "ref_id": ref, "path": str(path), "status": status}

    elif kind in ("fold", "grp"):
        return download_folder(ref, output_dir, context, index=index)

    elif kind == "crs":
        return download_folder(ref, output_dir, context, index=index, is_course=True)

    else:
        raise ValueError(f"Nicht unterstützter Objekttyp: {kind}")


# ===================================================================
#  download_folder  —  Recursively download folder contents
# ===================================================================

def download_folder(ref_id, output_dir, context, index=None, is_course=False, delay=0.25):
    """Recursively download all files from an ILIAS folder or course."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if index is None:
        index = DownloadIndex(root)

    # First discover the structure
    print(f"  Struktur wird geladen (ref_id={ref_id})...", flush=True)
    tree = discover_course(ref_id, context, delay=delay)

    # Collect all files recursively
    files = []
    _collect_files(tree, Path("."), files)
    print(f"  {len(files)} Dateien gefunden.", flush=True)

    results = {"type": "folder", "ref_id": str(ref_id), "title": tree.get("title"),
               "total": len(files), "downloaded": 0, "unchanged": 0, "errors": 0}

    for i, (fref, ftitle, fpath, frev) in enumerate(files, 1):
        try:
            folder = fpath.parent
            path, status = download_file(context, root, folder, fref,
                                        revision=frev, index=index)
            if status == "gespeichert":
                results["downloaded"] += 1
                time.sleep(delay)
            else:
                results["unchanged"] += 1
            print(f"  [{i}/{len(files)}] {status}: {path.relative_to(root)}", flush=True)
        except Exception as exc:
            results["errors"] += 1
            print(f"  [{i}/{len(files)}] FEHLER {fref}: {exc}", file=sys.stderr, flush=True)

    return results


def _collect_files(node, path, files):
    """Recursively collect (ref_id, title, path, revision) tuples."""
    for f in node.get("files", []):
        files.append((f["ref_id"], f["title"], path / (f["ref_id"] + "__" + safe_name(f["title"])), f.get("revision")))
    for child in node.get("children", []):
        child_path = path / (safe_name(child["title"]) + "__" + child["ref_id"])
        _collect_files(child, child_path, files)


# ===================================================================
#  direct_file_download  —  HTTP-only download without Playwright
# ===================================================================

def direct_file_download(ref_id, output_dir, cookies_path=None):
    """Download a file using plain HTTP with exported cookies (no Playwright).

    This is useful for parallel downloads or environments without Playwright.
    """
    import http.cookiejar

    url = f"{BASE}ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={ref_id}"

    # Build cookie jar from session.json
    cj = http.cookiejar.CookieJar()
    session_path = Path(cookies_path) if cookies_path else STATE
    if session_path.exists():
        session = json.loads(session_path.read_text(encoding="utf-8"))
        for cookie in session.get("cookies", []):
            if cookie.get("domain") == "ilias.unibe.ch":
                c = http.cookiejar.Cookie(
                    version=0, name=cookie["name"], value=cookie["value"],
                    port=None, port_specified=False,
                    domain=cookie["domain"], domain_specified=True, domain_initial_dot=False,
                    path=cookie.get("path", "/"), path_specified=True,
                    secure=cookie.get("secure", True),
                    expires=cookie.get("expires", None) if cookie.get("expires", -1) > 0 else None,
                    discard=True, comment=None, comment_url=None, rest={}, rfc2109=False,
                )
                cj.set_cookie(c)

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", "ilias-downloader/2")]

    request = urllib.request.Request(url)
    with opener.open(request, timeout=120) as response:
        content_disp = response.headers.get("Content-Disposition", "")
        from email.message import Message
        msg = Message()
        msg["content-disposition"] = content_disp
        filename = msg.get_filename()
        if not filename:
            raise RuntimeError("Keine Dateiantwort — Session möglicherweise abgelaufen")

        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        dest = output / safe_name(filename)
        temp = dest.with_suffix(dest.suffix + ".part")

        digest = hashlib.sha256()
        size = 0
        with temp.open("wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                size += len(chunk)

        temp.replace(dest)
        return {"path": str(dest), "size": size, "sha256": digest.hexdigest(), "filename": filename}


# ===================================================================
#  live_discovery  —  Derive recorder/room mappings from metadata
# ===================================================================

def live_discovery(findings_dir=None):
    """Derive recorder/room mappings from observed OpenCast metadata.

    Scans thumbnail URLs and HAR data for recorder device names,
    and maps them to room codes and livestream endpoints.
    """
    mappings = {}

    # Pattern: recorder names in dist.opencast thumbnail URLs
    # e.g. H4-extron-smp351-220_HS2026--_ffentliches_20260921-141300_S1R1_141.176s-player.jpg
    recorder_pattern = re.compile(
        r'/engage-player/([0-9a-f-]+)/[^/]+/([A-Za-z0-9]+-[A-Za-z0-9-]+)_'
        r'(?:HS|FS)\d{4}[^/]*_(\d{8})-(\d{6})_S(\d+)R(\d+)_'
    )

    # Known from README and user input
    known_recorders = {
        "H4-210-Aula-ext352": {
            "building": "Hauptgebäude 4",
            "room": "Aula 210",
            "hls_url": "https://livedist.opencast.unibe.ch/hls/H4-210-Aula-ext352/index.m3u8",
            "course": "Privatrecht I - OR Allgemeiner Teil 1__3721432",
            "evidence": "BEWIESEN (README + systemd config)",
        },
        "Fab6-001-ext352": {
            "building": "Fabrikstrasse 6",
            "room": "001",
            "hls_url": "https://livedist.opencast.unibe.ch/hls/Fab6-001-ext352/index.m3u8",
            "evidence": "BEWIESEN (User-Angabe)",
        },
    }

    # Scan for recorder names in known data
    if findings_dir:
        findings = Path(findings_dir)
        # Check network summary
        netsummary = findings / "meta" / "network_summary.json"
        if netsummary.exists():
            data = json.loads(netsummary.read_text(encoding="utf-8"))
            for url in data.get("opencast_calls", []):
                match = recorder_pattern.search(url)
                if match:
                    event_id, recorder_name, date_str, time_str, stream_num, recorder_num = match.groups()
                    if recorder_name not in known_recorders:
                        known_recorders[recorder_name] = {
                            "evidence": "ABGELEITET (aus Thumbnail-URL)",
                        }
                    known_recorders[recorder_name].setdefault("observed_events", []).append({
                        "event_id": event_id,
                        "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}",
                        "time": f"{time_str[:2]}:{time_str[2:4]}",
                        "stream": int(stream_num),
                        "recorder": int(recorder_num),
                    })

        # Also scan thumbnail filenames from dist.opencast
        thumb_pattern = re.compile(r'([A-Z][A-Za-z0-9]+-[a-z]+-[a-z0-9]+-\d+)')
        for url in data.get("opencast_calls", []):
            if "player.jpg" in url:
                m = thumb_pattern.search(url.split("/")[-1])
                if m:
                    name = m.group(1)
                    if name not in known_recorders:
                        known_recorders[name] = {
                            "evidence": "ABGELEITET (aus Thumbnail-Dateiname)",
                            "hls_url_hypothesis": f"https://livedist.opencast.unibe.ch/hls/{name}/index.m3u8",
                        }

    return known_recorders


# ===================================================================
#  stream_download_with_resume  —  Resumable MP4 download
# ===================================================================

def stream_download_with_resume(url, path, expected_size=None):
    """Download a large file with Range Request resume support.

    Designed for dist.opencast.unibe.ch MP4 files which support
    Accept-Ranges: bytes, ETag, and Last-Modified.
    """
    temp = path.with_suffix(path.suffix + ".part")
    headers = {"User-Agent": "ilias-downloader/2"}

    # Resume if partial download exists
    start_byte = 0
    if temp.exists():
        start_byte = temp.stat().st_size
        if start_byte > 0:
            headers["Range"] = f"bytes={start_byte}-"
            print(f"    Resume ab {start_byte / 1024 / 1024:.1f} MiB", flush=True)

    request = urllib.request.Request(url, headers=headers)
    digest = hashlib.sha256()
    size = start_byte

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            content_type = response.headers.get_content_type()

            if status == 416:  # Range Not Satisfiable — file already complete
                if temp.exists():
                    temp.replace(path)
                    return temp.stat().st_size, None
                raise RuntimeError("Range Not Satisfiable und keine Teildatei vorhanden")

            if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                raise RuntimeError(f"Unerwarteter Medientyp: {content_type}")

            mode = "ab" if (status == 206 and start_byte > 0) else "wb"
            if mode == "wb":
                start_byte = 0
                size = 0

            with temp.open(mode) as target:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    target.write(block)
                    digest.update(block)
                    size += len(block)

        if size < 1024 * 1024:
            raise RuntimeError("Mediendatei ist unerwartet klein")

        temp.replace(path)
        return size, digest.hexdigest()

    except Exception:
        # Keep partial file for resume
        raise


# ===================================================================
#  CLI
# ===================================================================

def _make_context(state_path=None):
    """Create a Playwright browser context with stored session."""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    browser = launch_browser(p, headless=True)
    sp = Path(state_path) if state_path else STATE
    context = browser.new_context(storage_state=str(sp))
    return p, browser, context


def cmd_inspect(args):
    p, browser, context = _make_context(args.state)
    try:
        result = inspect_url(args.url, context)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        browser.close()
        p.stop()


def cmd_discover(args):
    p, browser, context = _make_context(args.state)
    try:
        tree = discover_course(args.ref_id, context, max_pages=args.max_pages)
        output = json.dumps(tree, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"Gespeichert: {args.output}")
        else:
            print(output)
    finally:
        browser.close()
        p.stop()


def cmd_opencast(args):
    p, browser, context = _make_context(args.state)
    try:
        result = opencast_inspect(args.ref_id, context)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        browser.close()
        p.stop()


def cmd_download(args):
    p, browser, context = _make_context(args.state)
    try:
        output = Path(args.output) if args.output else Path(".")
        result = download_url(args.url, str(output), context)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    finally:
        browser.close()
        p.stop()


def cmd_download_folder(args):
    p, browser, context = _make_context(args.state)
    try:
        output = Path(args.output) if args.output else Path(".")
        result = download_folder(args.ref_id, str(output), context)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    finally:
        browser.close()
        p.stop()


def cmd_direct_download(args):
    output = Path(args.output) if args.output else Path(".")
    result = direct_file_download(args.ref_id, str(output), args.state)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_live_discovery(args):
    findings = args.findings if args.findings else str(HERE.parent / "Findings")
    result = live_discovery(findings)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", type=str, help="Path to Playwright session.json")

    parser = argparse.ArgumentParser(description=__doc__,
                                     parents=[common],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    # inspect-url
    p_inspect = sub.add_parser("inspect-url", parents=[common], help="Analyze an ILIAS URL")
    p_inspect.add_argument("url", help="ILIAS URL to inspect")
    p_inspect.add_argument("--json", action="store_true", help="Output as JSON")

    # discover-course
    p_discover = sub.add_parser("discover-course", parents=[common], help="Export course structure as JSON")
    p_discover.add_argument("ref_id", help="Course ref_id")
    p_discover.add_argument("--output", "-o", help="Output file path")
    p_discover.add_argument("--max-pages", type=int, default=500)

    # opencast-inspect
    p_opencast = sub.add_parser("opencast-inspect", parents=[common], help="Show OpenCast event details")
    p_opencast.add_argument("ref_id", help="Podcast module ref_id")

    # download-url
    p_dl = sub.add_parser("download-url", parents=[common], help="Auto-detect and download any ILIAS URL")
    p_dl.add_argument("url", help="ILIAS URL to download")
    p_dl.add_argument("--output", "-o", default=".", help="Output directory")

    # download-folder
    p_fold = sub.add_parser("download-folder", parents=[common], help="Recursively download folder contents")
    p_fold.add_argument("ref_id", help="Folder or course ref_id")
    p_fold.add_argument("--output", "-o", default=".", help="Output directory")

    # direct-download
    p_direct = sub.add_parser("direct-download", parents=[common], help="Download a file directly via HTTP (no browser)")
    p_direct.add_argument("ref_id", help="File ref_id")
    p_direct.add_argument("--output", "-o", default=".", help="Output directory")

    # live-discovery
    p_live = sub.add_parser("live-discovery", parents=[common], help="Derive recorder/room mappings")
    p_live.add_argument("--findings", help="Path to Findings directory")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        "inspect-url": cmd_inspect,
        "discover-course": cmd_discover,
        "opencast-inspect": cmd_opencast,
        "download-url": cmd_download,
        "download-folder": cmd_download_folder,
        "direct-download": cmd_direct_download,
        "live-discovery": cmd_live_discovery,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
