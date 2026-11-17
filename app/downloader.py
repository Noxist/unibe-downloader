"""Uni Bern ILIAS file downloader with interactive edu-ID authentication."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from email.message import Message
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlsplit
from sync_support import parse_date, week_folder, file_revision, DownloadIndex

BASE = "https://ilias.unibe.ch/"
HERE = Path(__file__).resolve().parent
if HERE.name == 'app':
    HERE = HERE.parent
CONFIG = HERE / 'app/config.json' if (HERE / 'app/config.json').exists() else HERE / 'config.json'
STOP = HERE / '.auth/stop-request'
STATE = HERE / ".auth" / "session.json"
SERVER_CHROMIUM = Path("/home/leandro/services/auto_reserve/data/playwright/chromium-1091/chrome-linux/chrome")
KINDS = {"ilobjfoldergui": "fold", "ilobjcoursegui": "crs",
         "ilobjgroupgui": "grp", "ilobjfilegui": "file"}


def launch_browser(p, headless):
    executable = os.environ.get("ILIAS_CHROMIUM", "")
    if not executable and SERVER_CHROMIUM.is_file():
        executable = str(SERVER_CHROMIUM)
    options = {"headless": headless}
    if executable:
        options["executable_path"] = executable
    return p.chromium.launch(**options)


class ConnectionFailure(RuntimeError):
    pass


def request_with_retry(operation, label, pause, log=None, attempts=3):
    from playwright.sync_api import Error as BrowserError
    for attempt in range(1, attempts + 1):
        check_stop()
        try:
            response = operation()
            if response is None:
                raise ConnectionFailure('Keine Serverantwort')
            if response.status not in (408, 425, 429, 500, 502, 503, 504):
                return response
            status = response.status
            if hasattr(response, 'dispose'):
                response.dispose()
            raise ConnectionFailure(f'HTTP {status}')
        except (BrowserError, ConnectionFailure) as exc:
            if log:
                log.emit('connection_retry', label=label, attempt=attempt,
                         error_type=type(exc).__name__)
            if attempt == attempts:
                raise ConnectionFailure(f'{label}: Verbindung nach {attempts} Versuchen fehlgeschlagen.') from exc
            delay = 2 ** attempt
            print(f'  [Verbindung] {label}: Versuch {attempt}/{attempts} fehlgeschlagen; erneut in {delay}s.', flush=True)
            pause(delay)


def diagnostic_url(url):
    """Keep routing information, discard credentials, fragments and query values."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        return {"scheme": parts.scheme}
    # edu-ID can put session identifiers into URL paths as well as queries.
    path = parts.path if parts.hostname == "ilias.unibe.ch" else "[redacted]"
    path = re.sub(r";.*", ";[redacted]", path)
    return {"host": parts.hostname, "path": path,
            "query_keys": sorted(parse_qs(parts.query, keep_blank_values=True))}


class BrowserLog:
    def __init__(self, context, operation):
        folder = HERE / ".auth" / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.path = folder / f"{operation}-{stamp}.jsonl"
        self.pages = []
        self.emit("start", operation=operation)
        print(f"Browser-Diagnose: {self.path}")
        context.on("page", self.attach)
        for page in context.pages:
            self.attach(page)

    def emit(self, event, **data):
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                     "event": event, **data}, ensure_ascii=False) + "\n")

    def attach(self, page):
        if page in self.pages:
            return
        self.pages.append(page)
        tab = len(self.pages)
        self.emit("tab_open", tab=tab, url=diagnostic_url(page.url))
        page.on("close", lambda: self.emit("tab_close", tab=tab))
        page.on("crash", lambda: self.emit("tab_crash", tab=tab))
        page.on("framenavigated", lambda frame: self.emit(
            "navigation", tab=tab, main_frame=frame == page.main_frame,
            url=diagnostic_url(frame.url)))
        page.on("request", lambda req: self.emit(
            "request", tab=tab, method=req.method, resource=req.resource_type,
            url=diagnostic_url(req.url)))
        page.on("response", lambda response: self.emit(
            "response", tab=tab, status=response.status, url=diagnostic_url(response.url)))
        page.on("requestfailed", lambda req: self.emit(
            "request_failed", tab=tab, resource=req.resource_type,
            reason=(re.search(r'ERR_[A-Z_]+', req.failure or '') or ['unbekannt'])[0],
            url=diagnostic_url(req.url)))
        # Console/error messages can contain credentials or tokens: log occurrence only.
        page.on("console", lambda msg: self.emit("console", tab=tab, level=msg.type))
        page.on("pageerror", lambda error: self.emit("javascript_error", tab=tab))
        page.on("domcontentloaded", lambda: self.snapshot(page, "dom_loaded"))

    def snapshot(self, page, reason):
        if page.is_closed():
            return
        try:
            structure = page.evaluate("""() => ({
                ready: document.readyState,
                passwordFields: document.querySelectorAll('input[type=password]').length,
                forms: [...document.forms].map(f => ({method: f.method,
                    inputTypes: [...f.querySelectorAll('input')].map(i => i.type)})),
                logoutGlyphs: document.querySelectorAll('.glyphicon-logout').length,
                loginGlyphs: document.querySelectorAll('.glyphicon-login').length,
                logoutControls: [...document.querySelectorAll('a,button,[role=button]')]
                    .filter(e => /logout|abmelden|ausloggen|sign out/i.test(
                        [e.textContent,e.getAttribute('aria-label'),e.getAttribute('href'),
                         e.getAttribute('onclick')].join(' ')))
                    .map(e => ({tag:e.tagName, hasHref:e.hasAttribute('href'),
                        hasOnclick:e.hasAttribute('onclick'),
                        visible:!!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)})),
                repositoryTitles: document.querySelectorAll('.il_ContainerItemTitle,.il-item-title').length
            })""")
            self.emit("page_structure", reason=reason, tab=self.pages.index(page) + 1,
                      url=diagnostic_url(page.url), structure=structure,
                      authenticated=authenticated(page))
        except Exception as exc:
            self.emit("snapshot_unavailable", reason=reason, error_type=type(exc).__name__)

    def snapshot_all(self, reason):
        for page in self.pages:
            self.snapshot(page, reason)


def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:100]
    if not name:
        name = "Unbenannt"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I):
        name = "_" + name
    return name


def local_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Dateipfad liegt ausserhalb des Download-Ordners.")
    return path


def object_id(url):
    parts = urlsplit(urljoin(BASE, url))
    if parts.scheme != "https" or parts.netloc != "ilias.unibe.ch":
        return None
    query = {k.lower(): v[0].lower() for k, v in parse_qs(parts.query).items()}
    if query.get("cmd", "") not in ("", "view", "render", "sendfile", "download", "show", "infoScreen".lower()):
        return None
    match = re.search(r"(?:^|_)(crs|fold|grp|file)_(\d+)(?:\.html|$)", parts.path)
    match = match or re.fullmatch(r"(crs|fold|grp|file)_(\d+)(?:_download)?", query.get("target", ""))
    if not match:
        match = re.fullmatch(r"/(?:go/)?(crs|fold|grp|file)/(\d+)/?", parts.path)
    if match:
        return match.group(1), match.group(2)
    kind = KINDS.get(query.get("cmdclass", ""))
    ref = query.get("ref_id", "")
    if query.get("cmd") == "infoscreen" and ref.isdigit():
        return "info", ref
    if kind and ref.isdigit():
        return kind, ref
    return None


def goto(kind, ref):
    return f"{BASE}goto.php?target={kind}_{ref}"


def parse_items(html, page_url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
    # Restrict traversal to repository entries, excluding breadcrumbs/navigation.
    selectors = ".il_ContainerItemTitle a[href], .il-item-title a[href]"
    result = {}
    for anchor in soup.select(selectors):
        if anchor["href"].startswith("#"):
            continue
        url = urljoin(page_url, anchor["href"])
        key = object_id(url)
        if key:
            result[key] = (anchor.get_text(" ", strip=True) or key[1], url)
    return result


def session_expansions(html, page_url, ref):
    """Follow only explicit read-only 'show more sessions' links for this course."""
    from bs4 import BeautifulSoup
    result = set()
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    for anchor in soup.select('a[href]'):
        url = urljoin(page_url, anchor['href'])
        query = parse_qs(urlsplit(url).query)
        if object_id(url) == ('crs', str(ref)) and any(
                query.get(key) == ['1'] for key in ('crs_next_sess', 'crs_prev_sess')):
            result.add(url)
    return sorted(result)


AUTH_MARKERS = (
    'a[href*="logout" i], .glyphicon-logout, '
    'button[onclick*="logout" i], '
    'button[aria-label="Logout" i], button[aria-label="Abmelden" i], '
    'a[aria-label="Logout" i], a[aria-label="Abmelden" i]'
)


def authenticated(page):
    if page.is_closed():
        return False
    parts = urlsplit(page.url)
    if parts.scheme != "https" or parts.netloc != "ilias.unibe.ch":
        return False
    if parts.path.lower().endswith('/login.php'):
        return False
    if page.locator('input[type="password"]:visible').count():
        return False
    # ILIAS 10 uses a logout glyph in the user menu, not necessarily an href.
    if page.locator(AUTH_MARKERS).count() > 0:
        return True
    # The SAML callback can land on ilias.php before the user menu is rendered.
    return parts.path.lower().endswith('/ilias.php')


def find_authenticated_page(context):
    for candidate in reversed(context.pages):
        if candidate.is_closed():
            continue
        # input() blocks Playwright's event dispatch. Pump it before checking URLs
        # so redirects/new tabs opened during the manual login are visible.
        candidate.wait_for_timeout(200)
        if authenticated(candidate):
            return candidate
    return None


def login_diagnostics(context):
    """Only structural information; never cookies, URL parameters or page text."""
    result = []
    for page in context.pages:
        if not page.is_closed():
            parts = urlsplit(page.url)
            result.append({"host": parts.hostname, "path": parts.path,
                           "logout_markers": page.locator(AUTH_MARKERS).count(),
                           "password_form": bool(page.locator('input[type="password"]:visible').count())})
    atomic_json(HERE / ".auth" / "login-diagnostics.json", result)


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def save_state(context):
    atomic_json(STATE, context.storage_state())


def login(p, config):
    browser = launch_browser(p, headless=False)
    context = browser.new_context()
    log = BrowserLog(context, "login")
    try:
        page = context.new_page()
        page.goto(goto("crs", config["courses"][0]["ref_id"]))
        print("Im Browser bei Uni Bern / SWITCH edu-ID anmelden (inkl. MFA).")
        print("Danach hier Enter druecken. Zugangsdaten bleiben im Browser.")
        while True:
            if input().strip().lower() == "q":
                log.emit("login_cancelled")
                return
            candidate = find_authenticated_page(context)
            log.snapshot_all("login_check")
            if candidate is not None:
                log.emit("login_recognized")
                break
            log.emit("login_not_recognized")
            login_diagnostics(context)
            print("Anmeldung noch nicht erkannt. Der Browser bleibt offen.")
            print("Auf ILIAS zurueckkehren und ggf. das Benutzermenue oben rechts oeffnen.")
            print("Dann erneut Enter druecken; q + Enter beendet den Login.")
            print("Technische Diagnose: .auth/login-diagnostics.json (ohne Zugangsdaten).")
        save_state(context)
        log.emit("session_saved")
        print("Sitzung lokal gespeichert.")
    finally:
        log.emit("browser_closing")
        browser.close()


def save_download(root, relative_dir, ref, filename, body):
    folder = local_path(root, relative_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = local_path(root, str(relative_dir / (ref + "__" + safe_name(filename))))
    digest = hashlib.sha256(body).hexdigest()
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return path, "unveraendert"
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(body)
    if path.exists():
        old_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        archive = local_path(root, str(relative_dir / ".versions" / old_digest / path.name))
        archive.parent.mkdir(parents=True, exist_ok=True)
        # Copy first: interrupted runs retain the previous file at its usual path.
        archive.write_bytes(path.read_bytes())
    temp.replace(path)
    return path, "gespeichert"


def download(context, root, folder, ref, revision=None, index=None, verify=False, log=None):
    cached, record = index.candidate(ref) if index else (None, {})
    def reuse():
        filename = record['filename']
        path, _ = save_download(root, folder, ref, filename, cached.read_bytes())
        index.remember(ref, path, revision, filename, {
            'etag': record.get('etag'), 'last-modified': record.get('last_modified')})
        return path, 'vorhanden (kein Download)'
    if cached and revision and revision == record.get('revision') and not verify:
        return reuse()
    headers = {}
    if cached and not verify:
        if record.get('etag'):
            headers['If-None-Match'] = record['etag']
        elif record.get('last_modified'):
            headers['If-Modified-Since'] = record['last_modified']
    # Use an explicit read-only download endpoint; never follow arbitrary actions.
    url = f"{BASE}ilias.php?baseClass=ilRepositoryGUI&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id={ref}"
    response = request_with_retry(
        lambda: context.request.get(url, timeout=120000, headers=headers),
        f'Datei {ref}', time.sleep, log)
    try:
        if response.status == 304 and cached:
            return reuse()
        if not response.ok or urlsplit(response.url).netloc != "ilias.unibe.ch":
            raise RuntimeError(f"Download fehlgeschlagen (HTTP {response.status}).")
        disposition = response.headers.get("content-disposition", "")
        message = Message()
        message["content-disposition"] = disposition
        filename = message.get_filename()
        if not filename:
            raise RuntimeError("Keine Dateiantwort erhalten; Sitzung oder Download-Link pruefen.")
        body = response.body()
        # A login/error page must never replace an existing document.
        if b'type="password"' in body[:100000].lower() or b"doStandardAuthentication" in body[:100000]:
            raise RuntimeError("Login-Seite statt Datei empfangen.")
        path, status = save_download(root, folder, ref, filename, body)
        if index:
            index.remember(ref, path, revision, filename, response.headers)
        return path, status
    finally:
        response.dispose()


def sync(p, config, dry_run, verify=False):
    if not STATE.exists():
        raise RuntimeError("Zuerst: python downloader.py login")
    root = (HERE / config["output"]).resolve()
    index = DownloadIndex(root)
    browser = launch_browser(p, headless=True)
    context = browser.new_context(storage_state=str(STATE))
    log = BrowserLog(context, "sync")
    errors = 0
    found = 0
    stored = 0
    unchanged = 0
    started = time.monotonic()
    print(f"\n{'VORSCHAU' if dry_run else 'DOWNLOAD-UPDATE'} | {len(config['courses'])} Kurse | Ziel: {root}", flush=True)
    try:
        last_request = 0.0
        for course_number, course in enumerate(config["courses"], 1):
            print(f"\n[Kurs {course_number}/{len(config['courses'])}] {course['name']}", flush=True)
            course_folder = Path(safe_name(course["name"]) + "__" + str(course["ref_id"]))
            if not dry_run:
                local_path(root, course_folder).mkdir(parents=True, exist_ok=True)
            queue = [("crs", str(course["ref_id"]), course_folder, goto('crs', course['ref_id']), None)]
            visited = set()
            jobs = {}
            while queue:
                check_stop()
                kind, ref, folder, url, session_day = queue.pop(0)
                query = parse_qs(urlsplit(url).query)
                key = (kind, ref, str(session_day), tuple((name, tuple(query.get(name, [])))
                                       for name in ('crs_next_sess', 'crs_prev_sess')))
                if key in visited:
                    continue
                if len(visited) >= config["max_pages"]:
                    raise RuntimeError("Seitenlimit erreicht; Ergebnis unvollstaendig.")
                visited.add(key)
                print(f'  [Pruefen {len(visited)}] {folder.relative_to(course_folder) if folder != course_folder else "Kursinhalt"}', flush=True)
                try:
                    # Only fetch the server-rendered HTML; no images, scripts, fonts or maps.
                    interval = config.get('delay_seconds', .25)
                    time.sleep(max(0, interval - (time.monotonic() - last_request)))
                    last_request = time.monotonic()
                    response = request_with_retry(
                        lambda: context.request.get(url, timeout=45000),
                        f'Seite {ref}', time.sleep, log)
                except ConnectionFailure as exc:
                    errors += 1
                    print(f'  [FEHLER] {exc} Seite uebersprungen; naechstes Update versucht sie erneut.', flush=True)
                    log.emit('page_failed', ref_id=ref, error_type=type(exc).__name__)
                    continue
                page_url = response.url
                status = response.status
                try:
                    html = response.text() if response.ok else ''
                    if not isinstance(html, str):
                        html = response.body().decode('utf-8', errors='replace')
                finally:
                    response.dispose()
                log.emit('html_response', ref_id=ref, status=status, url=diagnostic_url(page_url))
                if status >= 400:
                    errors += 1
                    print(f'  [FEHLER] Seite {ref}: HTTP {status}. Uebersprungen.', flush=True)
                    continue
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, 'html.parser')
                if not authenticated_html(page_url, soup):
                    raise RuntimeError('Sitzung abgelaufen oder Zugriff fehlt. Bitte Anmelden waehlen.')
                reference_day = datetime.now().date()
                if kind == 'info':
                    heading = soup.select_one('h1.ilHeader, h1')
                    session_day = (parse_date(heading.get_text(' ', strip=True), reference_day) if heading else None) or session_day
                    if session_day:
                        folder = week_folder(course_folder, session_day)
                items = parse_items(soup, page_url)
                if (kind in ('fold', 'grp') or session_day) and not dry_run:
                    local_path(root, folder).mkdir(parents=True, exist_ok=True)
                revisions = {}
                for anchor in soup.select('.il_ContainerItemTitle a[href], .il-item-title a[href]'):
                    identity = object_id(urljoin(page_url, anchor['href']))
                    if identity and identity[0] == 'file':
                        row = anchor.find_parent('div', class_='ilContainerListItemOuter')
                        revisions[identity[1]] = file_revision(row, anchor.get_text(' ', strip=True), reference_day)
                if kind == 'crs':
                    for expanded in session_expansions(soup, page_url, ref):
                        queue.append((kind, ref, folder, expanded, session_day))
                # Pagination is intentionally not guessed: fail visibly rather than claim completeness.
                pagination = soup.select('a[href*="_trows"], a[href*="_tbl"], .il-pagination a, .il-viewcontrol-pagination a')
                if pagination:
                    raise RuntimeError(f"Seitennavigation in Ordner {ref} erkannt; noch nicht unterstuetzt.")
                for (child_kind, child_ref), (title, child_url) in items.items():
                    if child_kind == "file":
                        # An explicit date in the file title also counts; upload dates don't.
                        day = session_day or parse_date(title, reference_day)
                        destination = week_folder(course_folder, day) if day else folder
                        jobs[(child_ref, str(destination))] = (title, destination, day, revisions.get(child_ref))
                    elif child_kind in ("fold", "grp", "info"):
                        day = parse_date(title, reference_day) or session_day
                        name = 'Termin__' + child_ref if child_kind == 'info' else safe_name(title) + '__' + child_ref
                        destination = week_folder(course_folder, day) if day else folder / name
                        queue.append((child_kind, child_ref, destination, child_url, day))
            # A file may be listed both in the course and inside dated sessions.
            # Keep each associated week, and omit the redundant undated placement.
            dated = {ref for (ref, _), (_, _, day, _) in jobs.items() if day}
            planned = [(ref, values) for (ref, _), values in jobs.items()
                       if ref not in dated or values[2]]
            print(f'  [Dateien] {len(planned)} Ablagen gefunden.', flush=True)
            for file_number, (ref, (title, folder, day, revision)) in enumerate(planned, 1):
                check_stop()
                if ref in dated and not day:
                    continue
                found += 1
                if dry_run:
                    print(f"  Datei {ref}: {folder} / {title}")
                else:
                    status = None
                    try:
                        path, status = download(context, root, folder, ref, revision, index, verify, log)
                        stored += status == 'gespeichert'
                        unchanged += status != 'gespeichert'
                        print(f"  [{file_number}/{len(planned)}] {status}: {path.relative_to(local_path(root, course_folder))}", flush=True)
                        log.emit('file_result', ref_id=ref, status=status)
                    except Exception as exc:
                        errors += 1
                        log.emit('file_failed', ref_id=ref, error_type=type(exc).__name__)
                        print(f"  FEHLER Datei {ref}: {exc}", file=sys.stderr)
                    if status == 'gespeichert':
                        time.sleep(config.get('delay_seconds', .25))
        save_state(context)
    finally:
        browser.close()
    print(f"\n{'UNVOLLSTAENDIG' if errors else 'FERTIG'} | {found} Dateiablagen | {stored} gespeichert | "
          f"{unchanged} unveraendert | {errors} Fehler | {time.monotonic() - started:.0f}s", flush=True)
    log.emit('sync_summary', placements=found, stored=stored, unchanged=unchanged, errors=errors)
    if not found and not errors:
        raise RuntimeError("Keine Dateien erkannt. Kurszugriff und Parser muessen geprueft werden.")
    return 1 if errors else 0


def add_course(config_path, config, url=None, name=None):
    url = (url or input('ILIAS-Kurslink oder ref_id: ')).strip()
    identity = ('crs', url) if url.isdigit() else object_id(url)
    # Pasted ordinary course URLs sometimes omit cmdClass.
    if identity is None and urlsplit(url).netloc == 'ilias.unibe.ch':
        query = parse_qs(urlsplit(url).query)
        ref = query.get('ref_id', [''])[0]
        if ref.isdigit() and not query.get('cmd'):
            identity = ('crs', ref)
    if not identity or identity[0] != 'crs':
        raise ValueError('Bitte den Link zur Kurs-Inhaltsseite oder deren ref_id verwenden.')
    if any(str(course['ref_id']) == identity[1] for course in config.get('courses', [])):
        print('Dieser Kurs ist bereits in der Auswahl.')
        return
    name = (name or input('Ordnername, z.B. HS2026 - Strafrecht Uebungen: ')).strip()
    if not name:
        raise ValueError('Ein Kursname ist erforderlich.')
    config.setdefault('courses', []).append({'ref_id': int(identity[1]), 'name': name})
    atomic_json(config_path, config)
    print(f'Hinzugefuegt: {name}. Beim naechsten Update wird der Kurs mitgeladen.')


def main():
    global STATE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "sync", "add-course", "convert-pdfs"])
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--dry-run", action="store_true", help="Nur Dateien auflisten")
    parser.add_argument("--verify", action="store_true", help="Alle Dateiinhalte erneut laden und vergleichen")
    parser.add_argument('--course', type=int, help='Nur diesen konfigurierten Kurs aktualisieren')
    parser.add_argument('--downloads-only', action='store_true', help='Server: nur ILIAS und Podcasts laden')
    parser.add_argument('--state', type=Path, help='Playwright storage_state der bestehenden edu-ID-Automation')
    parser.add_argument("--url", help="Kurs-URL oder ref_id fuer add-course")
    parser.add_argument("--name", help="Lokaler Kursname fuer add-course")
    parser.add_argument("--interval", type=int, default=0, help="Alle N Sekunden synchronisieren (mindestens 60)")
    args = parser.parse_args()
    if args.interval and args.interval < 60:
        parser.error("--interval muss mindestens 60 sein")
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if args.state:
        STATE = args.state.resolve()
    if args.command == 'add-course':
        add_course(args.config, config, args.url, args.name)
        return 0
    config = select_courses(config, args.course)
    if not config.get("courses") and args.command != 'convert-pdfs':
        parser.error("Mindestens einen Kurs konfigurieren")
    for course in config["courses"]:
        if not str(course["ref_id"]).isdigit() or not course.get("name"):
            parser.error("Jeder Kurs benoetigt eine numerische ref_id und einen Namen")
    config.setdefault("delay_seconds", .25)
    config.setdefault("max_pages", 500)
    if config["delay_seconds"] < 0.1 or config["max_pages"] < 1:
        parser.error("delay_seconds >= 0.1 und max_pages >= 1 erforderlich")
    from playwright.sync_api import sync_playwright
    # Exclusive lock prevents simultaneous sync/login processes sharing local state.
    lock = HERE / ".sync.lock"
    try:
        handle = lock.open("x")
    except FileExistsError:
        raise RuntimeError("Ein Lauf ist aktiv. Nach einem Absturz ggf. .sync.lock entfernen.")
    try:
        STOP.unlink(missing_ok=True)
        if args.command == 'convert-pdfs':
            from pdf_conversion import convert_tree
            with handle:
                return 1 if convert_tree((HERE / config['output']).resolve())['errors'] else 0
        with handle, sync_playwright() as p:
            if args.command == "login":
                login(p, config)
                return 0
            while True:
                result = (sync(p, config, args.dry_run, args.verify) if args.downloads_only
                          else sync_with_pdfs(p, config, args.dry_run, args.verify))
                if not args.interval:
                    return result
                print(f"Naechster Lauf in {args.interval} Sekunden. Beenden mit Strg+C.")
                for _ in range(args.interval):
                    check_stop()
                    time.sleep(1)
    finally:
        lock.unlink(missing_ok=True)


def check_stop():
    if STOP.exists():
        raise KeyboardInterrupt


def sync_with_pdfs(p, config, dry_run=False, verify=False):
    if dry_run:
        return sync(p, config, True, verify)
    from pdf_conversion import convert_tree
    root = (HERE / config['output']).resolve()
    folders = {safe_name(c['name']) + '__' + str(c['ref_id']) for c in config['courses']} if config.get('_single_course') else None
    try:
        result = sync(p, config, False, verify)
    finally:
        # Also process successful downloads from an otherwise interrupted run.
        after = convert_tree(root, only_folders=folders)
    if result or after['errors']:
        return 1
    if config.get('remarkable_auto'):
        from remarkable import sync_tree
        check_stop()
        return sync_tree(root, only_folders=folders)
    return 0


def select_courses(config, course_id=None):
    result = dict(config)
    result['courses'] = [c for c in config.get('courses', [])
                         if str(c['ref_id']) == str(course_id)] if course_id is not None else [
                             c for c in config.get('courses', []) if c.get('enabled', True)]
    if course_id is not None and not result['courses']:
        raise ValueError(f'Kurs {course_id} ist nicht konfiguriert.')
    result['_single_course'] = course_id is not None
    return result


def authenticated_html(url, soup):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc == 'ilias.unibe.ch'
            and not parts.path.lower().endswith('/login.php')
            and not soup.select_one('input[type="password"]')
            and soup.select_one(AUTH_MARKERS) is not None)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Beendet.")
        sys.exit(130)
    except Exception as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        sys.exit(1)
