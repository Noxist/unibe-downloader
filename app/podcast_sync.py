"""Download published Uni Bern Opencast recordings into course Lecture folders."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from downloader import BASE, authenticated_html, launch_browser


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def player_data(html):
    marker = 'player.init('
    start = html.find(marker)
    if start < 0:
        raise RuntimeError('Opencast-Playerdaten fehlen.')
    start += len(marker)
    value, _ = json.JSONDecoder().raw_decode(html[start:].lstrip())
    return value


def events(html, page_url):
    soup = BeautifulSoup(html, 'html.parser')
    result = {}
    for anchor in soup.select('a[href]'):
        url = urljoin(page_url, anchor.get('href', ''))
        query = parse_qs(urlsplit(url).query)
        if query.get('cmdClass', [''])[0].lower() != 'xoctplayergui':
            continue
        if query.get('cmd', [''])[0].lower() != 'streamvideo':
            continue
        eid = query.get('eid', [''])[0]
        text = anchor.get_text(' ', strip=True)
        match = re.search(r'(20\d\d)-(\d\d)-(\d\d)\s+(\d\d):(\d\d)', text)
        # Excludes undated tutorial videos such as "Podcasts optimal nutzen".
        if eid and match:
            year, month, day, hour, minute = match.groups()
            result[eid] = {'url': url, 'title': text,
                           'date': f'{year}-{month}-{day}', 'time': f'{hour}{minute}'}
    return result


def best_mp4(data):
    candidates = []
    for stream in data.get('streams', []):
        for source in stream.get('sources', {}).get('mp4', []):
            url = source.get('src', '')
            parts = urlsplit(url)
            if parts.scheme != 'https' or not parts.hostname or not parts.hostname.endswith('.unibe.ch'):
                continue
            resolution = source.get('res') or {}
            candidates.append((int(resolution.get('w', 0)) * int(resolution.get('h', 0)), url))
    if not candidates:
        raise RuntimeError('Keine erlaubte MP4-Quelle in den Playerdaten.')
    return max(candidates)[1]


def stream_download(url, path):
    """Download a media file with Range Request resume support.

    dist.opencast.unibe.ch supports Accept-Ranges: bytes, ETag, and
    Last-Modified.  Partial .part files are kept on failure so the next
    run can resume instead of re-downloading from scratch.
    """
    temp = path.with_suffix(path.suffix + '.part')
    headers = {'User-Agent': 'ilias-downloader/2'}

    # Resume from partial download if available
    start_byte = 0
    if temp.exists() and temp.stat().st_size > 0:
        start_byte = temp.stat().st_size
        headers['Range'] = f'bytes={start_byte}-'

    request = urllib.request.Request(url, headers=headers)
    digest = hashlib.sha256()
    size = start_byte

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            content_type = response.headers.get_content_type()

            if status == 416:
                # Range Not Satisfiable — partial file is already complete
                if temp.exists():
                    size = temp.stat().st_size
                    digest.update(temp.read_bytes())
                    temp.replace(path)
                    return size, digest.hexdigest()

            if not (content_type.startswith('video/') or content_type == 'application/octet-stream'):
                raise RuntimeError(f'Unerwarteter Medientyp: {content_type}')

            # If server responded 200 instead of 206, restart from scratch
            mode = 'ab' if (status == 206 and start_byte > 0) else 'wb'
            if mode == 'wb':
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
            raise RuntimeError('Mediendatei ist unerwartet klein.')
        temp.replace(path)
        return size, digest.hexdigest()
    except Exception:
        # Keep partial file for resume on next attempt
        if start_byte > 0:
            print(f'    Teildatei erhalten ({size / 1024 / 1024:.1f} MiB) für Resume.', flush=True)
        raise


def sync_podcasts(playwright, config, state):
    root = Path(config['output']).resolve()
    index_path = root / '.podcast-index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    browser = launch_browser(playwright, headless=True)
    context = browser.new_context(storage_state=str(state))
    errors = downloaded = unchanged = 0
    try:
        for course in config.get('courses', []):
            podcast_ref = course.get('podcast_ref')
            if not podcast_ref:
                continue
            list_url = (BASE + 'ilias.php?baseClass=ilobjplugindispatchgui&cmdNode=qp:qi&'
                        f'cmdClass=ilObjOpenCastGUI&cmd=showContent&ref_id={podcast_ref}')
            response = context.request.get(list_url, timeout=60000)
            html = response.text()
            soup = BeautifulSoup(html, 'html.parser')
            if not response.ok or not authenticated_html(response.url, soup):
                raise RuntimeError(f'Podcastliste {podcast_ref} nicht authentifiziert erreichbar.')
            found = events(html, response.url)
            response.dispose()
            print(f"  [Podcasts] {course['name']}: {len(found)} veröffentlichte Folgen", flush=True)
            folder = root / (course['name'] + '__' + str(course['ref_id']))
            for eid, event in sorted(found.items(), key=lambda pair: (pair[1]['date'], pair[1]['time'])):
                year = event['date'][:4]
                name = f"{event['date']}_{event['time']}__{eid[:8]}__Vorlesung.mp4"
                path = folder / year / 'Lectures' / name
                relative_path = str(path.relative_to(root))
                old = index.get(eid, {})
                if path.is_file() and path.stat().st_size == old.get('size') and old.get('sha256'):
                    unchanged += 1
                    continue
                uploaded_paths = {record.get('relative_path') for record in index.values()
                                  if record.get('drive_uploaded')}
                if not path.exists() and relative_path in uploaded_paths:
                    # The verified Drive copy is the archive; avoid re-downloading
                    # a local podcast that was deliberately removed after upload.
                    unchanged += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    player = context.request.get(event['url'], timeout=60000)
                    if not player.ok:
                        raise RuntimeError(f'Player HTTP {player.status}')
                    media_url = best_mp4(player_data(player.text()))
                    player.dispose()
                    size, digest = stream_download(media_url, path)
                    index[eid] = {'relative_path': str(path.relative_to(root)), 'size': size,
                                  'sha256': digest, 'title': event['title'], 'status': 'downloaded'}
                    atomic_json(index_path, index)
                    downloaded += 1
                    print(f'    gespeichert: {path.relative_to(root)} ({size / 1024 / 1024:.0f} MiB)', flush=True)
                except Exception as exc:
                    errors += 1
                    print(f'    FEHLER {eid[:8]}: {type(exc).__name__}: {exc}', flush=True)
    finally:
        context.close()
        browser.close()
    print(f'Podcasts: {downloaded} gespeichert, {unchanged} unverändert, {errors} Fehler.', flush=True)
    return 1 if errors else 0
