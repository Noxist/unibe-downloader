#!/usr/bin/env python3
"""Schedule and archive authorized ILIAS/Opencast HLS lectures on Linux.

The HTML exports are used only to discover local lecture dates and times. The
HLS URL is configured explicitly because a room/encoder URL is not safely
inferable from an ILIAS event id.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ZONE = dt.timezone(dt.timedelta(hours=1), "Europe/Zurich")
# Europe/Zurich is CET/CEST. zoneinfo is used when available so DST changes
# are handled correctly instead of relying on a fixed offset.
try:
    from zoneinfo import ZoneInfo
    ZONE = ZoneInfo("Europe/Zurich")
except Exception:
    pass


if dt.datetime(2026, 9, 15, tzinfo=ZONE).utcoffset() != dt.timedelta(hours=2):
    class ZurichFallback(dt.tzinfo):
        def utcoffset(self, value):
            return dt.timedelta(hours=2) if self.dst(value) else dt.timedelta(hours=1)

        def dst(self, value):
            if value is None:
                return dt.timedelta(0)
            year = value.year
            march = max(day for day in range(25, 32)
                        if dt.date(year, 3, day).weekday() == 6)
            october = max(day for day in range(25, 32)
                          if dt.date(year, 10, day).weekday() == 6)
            naive = value.replace(tzinfo=None)
            return dt.timedelta(hours=1) if dt.datetime(year, 3, march, 2) <= naive < dt.datetime(year, 10, october, 3) else dt.timedelta(0)

        def tzname(self, value):
            return "CEST" if self.dst(value) else "CET"

    ZONE = ZurichFallback()


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])


def html_text(path):
    parser = TextParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    return " ".join(" ".join(parser.parts).split())


def parse_date_time(text, default_year):
    """Find common German/ISO date and time pairs in exported ILIAS text."""
    date_patterns = [
        r"\b(\d{1,2})[.]\s*(\d{1,2})[.]\s*(20\d{2})\b",
        r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b",
    ]
    date_match = None
    for pattern in date_patterns:
        date_match = re.search(pattern, text)
        if date_match:
            values = [int(value) for value in date_match.groups()]
            if pattern.startswith(r"\b(20"):
                year, month, day = values
            elif values[2] >= 2000:
                day, month, year = values
            else:
                year, month, day = default_year, values[1], values[0]
            break
    if not date_match:
        return None
    times = re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if not times:
        return None
    hour, minute = (int(value) for value in times[0])
    try:
        return dt.datetime(year, month, day, hour, minute, tzinfo=ZONE)
    except ValueError:
        return None


def parse_exports(folder, default_year):
    events = []
    for path in sorted(folder.rglob("*.html")) + sorted(folder.rglob("*.htm")):
        raw = path.read_text(encoding="utf-8", errors="replace")
        # SingleFile exports contain embedded CSS and scripts. Prefer the
        # structured event identifiers instead of scanning the whole document.
        identifiers = re.findall(
            r'class=["\']?c-entity__primary-identifier["\']?[^>]*>(.*?)</div>',
            raw, re.I | re.S)
        for value in identifiers:
            value = re.sub(r"<[^>]+>", " ", value)
            value = " ".join(value.split())
            match = re.search(r"^(.*?)\s+-\s+(20\d{2})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})$", value)
            if not match:
                continue
            title, year, month, day, hour, minute = match.groups()
            start = dt.datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=ZONE)
            events.append({"start": start.isoformat(), "title": title.strip(), "source": str(path)})
        if identifiers:
            continue
        text = html_text(path)
        start = parse_date_time(text, default_year)
        if start:
            events.append({"start": start.isoformat(), "title": path.stem, "source": str(path)})
    unique = {}
    for event in events:
        unique[(event["start"], event["title"])] = event
    return sorted(unique.values(), key=lambda event: event["start"])


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ilias-live-recorder/1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


def parse_playlist(text, playlist_url):
    if not text.startswith("#EXTM3U"):
        raise ValueError("Response is not an HLS playlist")
    if any(tag in text for tag in ("#EXT-X-STREAM-INF:", "#EXT-X-KEY:", "#EXT-X-MAP:", "#EXT-X-BYTERANGE:")):
        raise ValueError("Unsupported HLS playlist feature")
    sequence = 0
    duration = None
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1])
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line and not line.startswith("#"):
            if duration is None:
                raise ValueError("Segment without EXTINF")
            items.append({"sequence": sequence, "url": urllib.parse.urljoin(playlist_url, line),
                          "duration": duration, "discontinuity": False})
            sequence += 1
            duration = None
    if not items:
        raise ValueError("Empty HLS playlist")
    return items


def atomic(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def save_segment(item, folder):
    path = folder / f"{item['sequence']:08d}.ts"
    if path.exists() and path.stat().st_size:
        return
    last_error = None
    for attempt in range(4):
        try:
            body = fetch(item["url"])
            if not body or len(body) % 188 or body[:1] != b"\x47":
                raise ValueError("Invalid MPEG-TS response")
            atomic(path, body)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"segment {item['sequence']} failed: {last_error}")


def numbered_segment_url(url, sequence):
    parsed = urllib.parse.urlsplit(url)
    match = re.search(r"/(\d+)(\.[^/]*)$", parsed.path)
    if not match:
        raise ValueError(f"Cannot derive numbered segment URL from {url}")
    path = parsed.path[:match.start(1)] + str(sequence) + match.group(2)
    return urllib.parse.urlunsplit(parsed._replace(path=path))


def backfill_segments(first_item, start_sequence, known, folder, pool, segment_duration):
    first_sequence = first_item["sequence"]
    if start_sequence >= first_sequence:
        return
    items = []
    for sequence in range(start_sequence, first_sequence):
        if sequence not in known:
            items.append({"sequence": sequence,
                          "url": numbered_segment_url(first_item["url"], sequence),
                          "duration": segment_duration, "discontinuity": False})
    if items:
        list(pool.map(lambda item: save_segment(item, folder), items))
        known.update({item["sequence"]: item for item in items})


def export_mp4(folder, items):
    playlist = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:3",
                f"#EXT-X-MEDIA-SEQUENCE:{items[0]['sequence']}"]
    for item in items:
        playlist.extend([f"#EXTINF:{item['duration']:.6f},", f"segments/{item['sequence']:08d}.ts"])
    playlist.append("#EXT-X-ENDLIST")
    atomic(folder / "local.m3u8", ("\n".join(playlist) + "\n").encode())
    output = folder / "lecture.mp4"
    if shutil.which("ffmpeg"):
        ffmpeg = ["ffmpeg"]
        input_path = str(folder / "local.m3u8")
        output_path = str(output)
    elif shutil.which("docker"):
        ffmpeg = ["docker", "run", "--rm", "-v", f"{folder.resolve()}:/work",
                  "linuxserver/ffmpeg:latest"]
        input_path = "/work/local.m3u8"
        output_path = "/work/lecture.mp4"
    else:
        raise RuntimeError("ffmpeg or Docker is required for MP4 export")
    subprocess.run(ffmpeg + ["-hide_banner", "-nostdin", "-v", "warning", "-n", "-i",
                             input_path, "-map", "0:v?", "-map", "0:a?", "-c", "copy",
                             "-movflags", "+faststart", output_path], check=True)
    if shutil.which("ffprobe"):
        probe = subprocess.check_output(["ffprobe", "-v", "error", "-show_format",
                                         "-show_streams", "-of", "json", str(output)])
    else:
        probe = json.dumps({"format": {"filename": str(output),
                                        "duration_seconds": sum(item["duration"] for item in items),
                                        "size": output.stat().st_size},
                            "note": "ffprobe unavailable; validated by FFmpeg export"},
                           indent=2).encode()
    atomic(folder / "ffprobe.json", probe)


def upload_to_drive(folder, remote, drive_path):
    destination = f"{remote}:{drive_path.strip('/')}/{folder.name}"
    for filename in ("lecture.mp4", "ffprobe.json"):
        source = folder / filename
        subprocess.run(["rclone", "copyto", str(source),
                        f"{destination}/{filename}", "--checksum"], check=True)
    subprocess.run(["rclone", "lsf", f"{destination}/lecture.mp4",
                    "--files-only"], check=True, stdout=subprocess.DEVNULL)


def integrate_recording(folder, event, args):
    """Publish a completed live capture into the shared course Lectures tree."""
    start = dt.datetime.fromisoformat(event["start"])
    lectures = args.integrated_root / args.course_folder / f"{start:%Y}" / "Lectures"
    lectures.mkdir(parents=True, exist_ok=True)
    destination = lectures / f"{start:%Y-%m-%d_%H%M}__live__Vorlesung.mp4"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(folder / "lecture.mp4", temporary)
    temporary.replace(destination)
    subprocess.run([args.transcribe_python, str(args.transcribe_script),
                    "--root", str(args.integrated_root),
                    "--model", args.transcription_model,
                    "--language", args.transcription_language], check=True)
    remote = f"{args.integrated_drive_remote.rstrip('/')}:{args.integrated_drive_path.strip('/')}"
    command = ["rclone", "copy", str(lectures),
               f"{remote}/{args.course_folder}/{start:%Y}/Lectures",
               "--exclude", "*.tmp", "--checksum"]
    if args.drive_root_folder_id:
        command.extend(["--drive-root-folder-id", args.drive_root_folder_id])
    subprocess.run(command, check=True)
    from lecture_archive import remove_verified_files, verify_drive_copy
    verified = verify_drive_copy(
        lectures,
        f"{remote}/{args.course_folder}/{start:%Y}/Lectures",
        args.drive_root_folder_id,
    )
    current_stem = destination.name.removesuffix('.mp4')
    current_artifacts = [path for path in verified
                         if path == destination
                         or path.name.startswith(current_stem + '.transcript.')]
    remove_verified_files(current_artifacts)
    return destination


def cleanup_uploaded(output, retention_hours):
    cutoff = time.time() - retention_hours * 3600
    for folder in output.iterdir() if output.exists() else ():
        if not folder.is_dir():
            continue
        state_path = folder / "state.json"
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text())
            uploaded_at = dt.datetime.fromisoformat(state["uploaded_at"])
            uploaded_timestamp = uploaded_at.timestamp()
        except (KeyError, OSError, ValueError, TypeError):
            continue
        if state.get("status") == "uploaded" and uploaded_timestamp <= cutoff:
            for path in folder.iterdir():
                if path.name != "state.json":
                    if path.is_dir():
                        import shutil
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            state["status"] = "cleaned"
            atomic(state_path, json.dumps(state, indent=2).encode())


def event_folder(event, output):
    start = dt.datetime.fromisoformat(event["start"])
    title = re.sub(r"[^A-Za-z0-9._-]+", "_", event["title"])[:80]
    return (output / f"{start:%Y-%m-%d}_{title}").resolve()


def is_completed(event, output):
    state_path = event_folder(event, output) / "state.json"
    if not state_path.exists():
        return False
    try:
        status = json.loads(state_path.read_text()).get("status")
    except (OSError, ValueError):
        return False
    return status in {"exported", "uploaded", "cleaned"}


def record(event, args):
    start = dt.datetime.fromisoformat(event["start"])
    now = dt.datetime.now(ZONE)
    capture_start = start - dt.timedelta(minutes=args.pre_roll_minutes)
    if capture_start > now:
        wait = (capture_start - now).total_seconds()
        print(f"Warte {wait:.0f}s bis {capture_start.isoformat()} | {event['title']}", flush=True)
        time.sleep(wait)
    folder = event_folder(event, args.output)
    segments = folder / "segments"
    segments.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "url": args.url, "title": event["title"], "event_start": event["start"], "items": []}
    known = {item["sequence"]: item for item in state.get("items", [])}
    deadline = start + dt.timedelta(minutes=args.max_minutes)
    idle_since = time.monotonic()
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        backfill_complete = state.get("backfill_complete", False)
        while dt.datetime.now(ZONE) < deadline:
            try:
                playlist = fetch(args.url).decode("utf-8-sig")
                current = parse_playlist(playlist, args.url)
                failures = 0
            except Exception as exc:
                failures += 1
                print(f"Playlist-Fehler {failures}/6: {type(exc).__name__}: {exc}", flush=True)
                if failures >= 6:
                    raise
                time.sleep(10)
                continue
            new = [item for item in current if item["sequence"] not in known]
            if new:
                idle_since = time.monotonic()
            if not backfill_complete and args.backfill_start is not None:
                backfill_segments(current[0], args.backfill_start, known, segments,
                                  pool, args.segment_duration)
                backfill_complete = True
                state["backfill_start"] = args.backfill_start
                state["backfill_complete"] = True
            for item in current:
                known[item["sequence"]] = item
            state["items"] = sorted(known.values(), key=lambda item: item["sequence"])
            state["last_playlist"] = dt.datetime.now(dt.timezone.utc).isoformat()
            atomic(state_path, json.dumps(state, indent=2).encode())
            atomic(folder / "latest.m3u8", playlist.encode())
            list(pool.map(lambda item: save_segment(item, segments), state["items"]))
            duration = sum(item["duration"] for item in state["items"])
            print(f"{dt.datetime.now(ZONE).isoformat(timespec='seconds')} segments={len(known)} duration={duration/60:.1f}min", flush=True)
            if "#EXT-X-ENDLIST" in playlist:
                state["stop_reason"] = "endlist"
                break
            if time.monotonic() - idle_since >= args.idle_seconds:
                state["stop_reason"] = "idle-timeout"
                break
            time.sleep(args.poll_seconds)
    items = state.get("items", [])
    if not items:
        raise RuntimeError("No segments recorded")
    numbers = [item["sequence"] for item in items]
    if numbers != list(range(numbers[0], numbers[-1] + 1)):
        raise RuntimeError("Sequence gap; refusing export")
    for item in items:
        if not (segments / f"{item['sequence']:08d}.ts").exists():
            raise RuntimeError("Missing segment; refusing export")
    state.update(stopped=dt.datetime.now(dt.timezone.utc).isoformat(),
                 duration_seconds=sum(item["duration"] for item in items), status="exporting")
    atomic(state_path, json.dumps(state, indent=2).encode())
    export_mp4(folder, items)
    state["status"] = "exported"
    atomic(state_path, json.dumps(state, indent=2).encode())
    if args.integrated_root:
        destination = integrate_recording(folder, event, args)
        state["integrated_path"] = str(destination)
        state["uploaded_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        state["status"] = "uploaded"
        atomic(state_path, json.dumps(state, indent=2).encode())
    elif args.gdrive_remote:
        upload_to_drive(folder, args.gdrive_remote, args.gdrive_path)
        state["uploaded_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        state["status"] = "uploaded"
        atomic(state_path, json.dumps(state, indent=2).encode())
    if args.integrated_root and state.get("status") == "uploaded":
        cleanup_uploaded(args.output, args.local_retention_hours)
        print("Drive-Upload geprüft; lokale Rohdaten gemäss Aufbewahrung bereinigt.", flush=True)
    else:
        print(f"Export fertig: {folder / 'lecture.mp4'}", flush=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html-dir", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=Path("recordings"))
    parser.add_argument("--year", type=int, default=dt.datetime.now(ZONE).year)
    parser.add_argument("--max-minutes", type=int, default=150)
    parser.add_argument("--pre-roll-minutes", type=int, default=5)
    parser.add_argument("--idle-seconds", type=int, default=180)
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--backfill-start", type=int, default=0,
                        help="first numbered segment to download before the live playlist")
    parser.add_argument("--no-backfill", dest="backfill_start", action="store_const", const=None,
                        help="disable downloading older numbered segments")
    parser.add_argument("--segment-duration", type=float, default=1.0,
                        help="duration used for backfilled segments in the local playlist")
    parser.add_argument("--gdrive-remote",
                        help="configured rclone remote name for Google Drive uploads")
    parser.add_argument("--gdrive-path", default="ILIAS Live Recordings",
                        help="Google Drive folder below the rclone remote")
    parser.add_argument("--local-retention-hours", type=float, default=24,
                        help="keep uploaded recordings locally for this many hours")
    parser.add_argument("--integrated-root", type=Path,
                        help="shared unibe-downloader staging root")
    parser.add_argument("--course-folder", default="Privatrecht I - OR Allgemeiner Teil 1__3721432")
    parser.add_argument("--transcribe-python",
                        default="/home/leandro/services/unibe-downloader/.venv/bin/python")
    parser.add_argument("--transcribe-script", type=Path,
                        default=Path("/home/leandro/services/unibe-downloader/app/transcribe.py"))
    parser.add_argument("--transcription-model", default="small")
    parser.add_argument("--transcription-language", default="de")
    parser.add_argument("--integrated-drive-remote", default="gdrive")
    parser.add_argument("--integrated-drive-path", default="1. Semester")
    parser.add_argument("--drive-root-folder-id")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--event-index", type=int)
    args = parser.parse_args()
    if args.gdrive_remote and args.local_retention_hours < 0:
        parser.error("--local-retention-hours must not be negative")
    cleanup_uploaded(args.output, args.local_retention_hours)
    events = parse_exports(args.html_dir, args.year)
    if not events:
        raise SystemExit("Keine Termine erkannt; HTML-Exportformat prüfen.")
    for index, event in enumerate(events):
        print(f"[{index}] {event['start']} | {event['title']} | {event['source']}")
    if args.list_only:
        return
    if args.event_index is not None:
        record(events[args.event_index], args)
        cleanup_uploaded(args.output, args.local_retention_hours)
        return
    now = dt.datetime.now(ZONE)
    current = [event for event in events
               if not is_completed(event, args.output)
               and dt.datetime.fromisoformat(event["start"]) <= now <
               dt.datetime.fromisoformat(event["start"]) + dt.timedelta(minutes=args.max_minutes)]
    upcoming = current + [event for event in events
                          if not is_completed(event, args.output)
                          and dt.datetime.fromisoformat(event["start"]) > now
                          and event not in current]
    if not upcoming:
        print("Keine zukünftige Veranstaltung im Export.")
        return
    for event in upcoming:
        record(event, args)
        cleanup_uploaded(args.output, args.local_retention_hours)


if __name__ == "__main__":
    main()
