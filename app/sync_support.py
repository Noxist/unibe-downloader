"""Date interpretation and durable incremental download bookkeeping."""
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

MONTHS = {
    'jan': 1, 'januar': 1, 'feb': 2, 'februar': 2, 'mär': 3, 'märz': 3,
    'maerz': 3, 'mar': 3, 'apr': 4, 'april': 4, 'mai': 5, 'may': 5,
    'jun': 6, 'juni': 6, 'jul': 7, 'juli': 7, 'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9, 'okt': 10, 'oktober': 10,
    'oct': 10, 'nov': 11, 'november': 11, 'dez': 12, 'dezember': 12,
    'dec': 12,
}


def parse_date(text, reference):
    """Read an explicit date or an ILIAS relative date; never guess a year."""
    text = text.strip().lower()
    for word, offset in [('vorgestern', -2), ('gestern', -1), ('heute', 0),
                         ('morgen', 1), ('übermorgen', 2)]:
        if re.match(r'^' + word + r'\b', text):
            return reference + timedelta(days=offset)
    match = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text)
    try:
        if match:
            return date(*map(int, match.groups()))
        match = re.search(r'\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b', text)
        if match:
            day, month, year = map(int, match.groups())
            return date(year, month, day)
        match = re.search(r'\b(\d{1,2})\.\s*([a-zäöü]+)\.?\s+(\d{4})\b', text)
        if match and match[2] in MONTHS:
            return date(int(match[3]), MONTHS[match[2]], int(match[1]))
    except ValueError:
        pass
    return None


def week_folder(course_folder, day):
    year, week, _ = day.isocalendar()
    return course_folder / str(year) / f'KW ({week:02d})'


def file_revision(row, title, reference):
    """Only accept explicit modification/version metadata as a skip signal."""
    if row is None:
        return None
    props = [x.get_text(' ', strip=True) for x in row.select('.il_ItemProperty')]
    reliable = False
    normalized = []
    for prop in props:
        day = parse_date(prop, reference)
        if day:
            reliable = True
            clock = re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', prop)
            prop = day.isoformat() + (' ' + clock[0] if clock else '')
        if re.search(r'\bversion\s*:\s*\S+', prop, re.I):
            reliable = True
        normalized.append(prop)
    if not reliable:
        return None
    return hashlib.sha256(json.dumps([title, normalized], ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    with path.open('rb') as handle:
        if hasattr(hashlib, 'file_digest'):
            return hashlib.file_digest(handle, 'sha256').hexdigest()
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
        return digest.hexdigest()


class DownloadIndex:
    def __init__(self, root):
        self.root = root.resolve()
        self.path = root / '.download-index.json'
        self.files = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def candidate(self, ref):
        record = self.files.get(str(ref), {})
        for relative in record.get('paths', []):
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError('Ungueltiger Pfad im Download-Index')
            if path.is_file() and file_hash(path) == record.get('sha256'):
                return path, record
        return None, record

    def remember(self, ref, path, revision, filename, headers=None):
        previous = self.files.get(str(ref), {})
        digest = file_hash(path)
        paths = previous.get('paths', []) if previous.get('sha256') == digest else []
        paths = sorted(set(paths + [path.relative_to(self.root).as_posix()]))
        self.files[str(ref)] = {'paths': paths, 'revision': revision,
                                'filename': filename, 'sha256': digest,
                                'etag': (headers or {}).get('etag'),
                                'last_modified': (headers or {}).get('last-modified')}
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.files, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)
