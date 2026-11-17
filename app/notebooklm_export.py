"""Build upload-ready NotebookLM folders without videos or duplicate formats."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import re
from datetime import datetime, timezone

USER_FORMATS = {'.pdf', '.txt', '.epub', '.png', '.jpg', '.jpeg', '.tif', '.tiff',
                '.bmp', '.gif', '.webp', '.doc', '.docx', '.odt', '.rtf',
                '.ppt', '.pptx', '.odp', '.xls', '.xlsx', '.ods'}
OFFICE_FORMATS = {'.doc', '.docx', '.odt', '.rtf', '.ppt', '.pptx', '.odp',
                  '.xls', '.xlsx', '.ods'}
SKIP_PARTS = {'NotebookLM', '.versions', '.pdf-work', '.auth'}


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(path, root):
    relative = path.relative_to(root)
    if any(part in SKIP_PARTS or part.startswith('.') for part in relative.parts):
        return None
    return relative


def convert_docx(source, target):
    """Portable Word-to-PDF fallback used when LibreOffice is unavailable."""
    from docx import Document
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from xml.sax.saxutils import escape

    document = Document(str(source))
    styles = getSampleStyleSheet()
    story = []
    for paragraph in document.paragraphs:
        text = escape(paragraph.text.strip())
        if not text:
            story.append(Spacer(1, 2 * mm))
            continue
        style = styles['Heading2'] if paragraph.style.name.startswith('Heading') else styles['BodyText']
        story.append(Paragraph(text, style))
        story.append(Spacer(1, 1.5 * mm))
    for table in document.tables:
        rows = [[Paragraph(escape(cell.text), styles['BodyText']) for cell in row.cells]
                for row in table.rows]
        if rows:
            item = Table(rows, repeatRows=1)
            item.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .25, colors.grey),
                                      ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
            story.extend([item, Spacer(1, 3 * mm)])
    if not story:
        story.append(Paragraph('(Leeres Dokument)', styles['BodyText']))
    SimpleDocTemplate(str(target), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
                      topMargin=15 * mm, bottomMargin=15 * mm).build(story)


def convert_office(source, target):
    from pdf_conversion import convert_document, find_office
    if source.suffix.lower() == '.docx' and not find_office():
        convert_docx(source, target)
        return
    with tempfile.TemporaryDirectory() as temporary:
        convert_document(source, target, Path(temporary))


def sources_for(course, root, extras):
    result = [(course, Path())]
    for extra_name in extras.get(course.name, []):
        extra = root / extra_name
        if extra.is_dir():
            result.append((extra, Path('Übungen')))
    return result


def flat_name(relative, prefix):
    """Return one descriptive, collision-resistant filename without directories."""
    parts = list(relative.parts)
    if prefix.parts and parts and parts[0] == 'Übungen':
        parts = parts[1:]
    filename = parts[-1]
    folders = parts[:-1]
    labels = []
    if prefix.parts:
        labels.append('Übungen')
    week = next((re.fullmatch(r'KW \((\d{1,2})\)', part) for part in folders
                 if re.fullmatch(r'KW \((\d{1,2})\)', part)), None)
    if week:
        labels.append(f'KW{int(week.group(1)):02d}')
    elif 'Lectures' in folders:
        labels.append('Lecture')
    elif folders:
        useful = [re.sub(r'__\d+$', '', part) for part in folders
                  if not re.fullmatch(r'20\d{2}', part)]
        labels.extend(useful or ['Allgemein'])
    else:
        labels.append('Allgemein')
    if filename.endswith('.transcript.txt'):
        filename = filename[:-len('.transcript.txt')] + '__Transkript.txt'
    clean_labels = [re.sub(r'[^0-9A-Za-zÄÖÜäöüß_-]+', '_', label).strip('_') for label in labels]
    return '__'.join(clean_labels + [filename])


def build(root, config=None):
    root = Path(root).resolve()
    extras = {}
    extra_source_names = set()
    if config:
        for course in config.get('courses', []):
            name = course['name'] + '__' + str(course['ref_id'])
            extras[name] = course.get('notebooklm_extra_sources', [])
            extra_source_names.update(extras[name])
    stats = {'copied': 0, 'converted': 0, 'unchanged': 0, 'errors': 0}
    courses = sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith('.'))
    for course in courses:
        # Exercise courses configured as extra sources belong exclusively to
        # their main lecture's NotebookLM inbox. Never create a second,
        # standalone subject folder for the same material.
        if course.name in extra_source_names:
            continue
        destination_root = course / 'NotebookLM'
        destination_root.mkdir(exist_ok=True)
        manifest_path = destination_root / '.manifest.json'
        previous = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
        ledger_path = course / '.notebooklm-export-index.json'
        ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
        manifest = {}
        selected = []
        for source_root, prefix in sources_for(course, root, extras):
            paths = [p for p in source_root.rglob('*') if p.is_file() and not p.is_symlink()]
            available_pdfs = {p.with_suffix('').relative_to(source_root).as_posix().casefold()
                              for p in paths if p.suffix.lower() == '.pdf' and safe_relative(p, source_root)}
            for source in paths:
                relative = safe_relative(source, source_root)
                if relative is None or source.suffix.lower() not in USER_FORMATS:
                    continue
                if source.name.endswith(('.transcript.vtt', '.transcript.json')):
                    continue
                if source.suffix.lower() in OFFICE_FORMATS and source.with_suffix('').relative_to(source_root).as_posix().casefold() in available_pdfs:
                    continue
                selected.append((source, prefix / relative))
        seen_content = set()
        used_names = set()
        for source, relative in selected:
            try:
                digest = file_hash(source)
                if digest in seen_content:
                    continue
                seen_content.add(digest)
                if source.suffix.lower() in OFFICE_FORMATS:
                    relative = relative.with_suffix('.pdf')
                name = flat_name(relative, Path('Übungen') if relative.parts and relative.parts[0] == 'Übungen' else Path())
                if name.casefold() in used_names:
                    stem, suffix = Path(name).stem, Path(name).suffix
                    name = f'{stem}__{digest[:8]}{suffix}'
                used_names.add(name.casefold())
                relative = Path(name)
                source_key = source.relative_to(root).as_posix()
                if ledger.get(source_key, {}).get('source_sha256') == digest:
                    stats['unchanged'] += 1
                    continue
                target = (destination_root / relative).resolve()
                if not target.is_relative_to(destination_root.resolve()):
                    raise ValueError('Ungültiger NotebookLM-Zielpfad')
                key = relative.as_posix()
                record = previous.get(key, {})
                if target.is_file() and record.get('source_sha256') == digest:
                    stats['unchanged'] += 1
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix(target.suffix + '.tmp')
                    if source.suffix.lower() in OFFICE_FORMATS:
                        convert_office(source, temporary)
                        stats['converted'] += 1
                    else:
                        shutil.copy2(source, temporary)
                        stats['copied'] += 1
                    temporary.replace(target)
                manifest[key] = {'source': source_key, 'source_sha256': digest}
            except Exception as exc:
                stats['errors'] += 1
                print(f'NotebookLM-FEHLER {source}: {type(exc).__name__}: {exc}', file=sys.stderr)
        # Remove only stale files recorded by our previous manifest. User-added
        # files are deliberately untouched.
        for stale in set(previous) - set(manifest):
            stale_path = (destination_root / stale).resolve()
            if stale_path.is_relative_to(destination_root.resolve()) and stale_path.is_file():
                stale_path.unlink()
        for directory in sorted((p for p in destination_root.rglob('*') if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        if not manifest:
            manifest_path.unlink(missing_ok=True)
            try:
                destination_root.rmdir()
            except OSError:
                pass
        else:
            temporary_manifest = manifest_path.with_suffix('.tmp')
            temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary_manifest.replace(manifest_path)
            print(f'NotebookLM {course.name}: {len(manifest)} neue Dateien in der Warteschlange', flush=True)
    print(f"NotebookLM: {stats['copied']} kopiert, {stats['converted']} konvertiert, "
          f"{stats['unchanged']} unverändert, {stats['errors']} Fehler.", flush=True)
    return 1 if stats['errors'] else 0


def acknowledge(root):
    """Mark queued files delivered and clear only the local queue copies."""
    root = Path(root).resolve()
    delivered = 0
    for course in sorted(path for path in root.iterdir() if path.is_dir()):
        queue = course / 'NotebookLM'
        manifest_path = queue / '.manifest.json'
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        ledger_path = course / '.notebooklm-export-index.json'
        ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
        for filename, record in manifest.items():
            queued = (queue / filename).resolve()
            if not queued.is_relative_to(queue.resolve()) or not queued.is_file():
                continue
            ledger[record['source']] = {
                'source_sha256': record['source_sha256'], 'filename': filename,
                'delivered_at': datetime.now(timezone.utc).isoformat(),
            }
            queued.unlink()
            delivered += 1
        temporary = ledger_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(ledger_path)
        manifest_path.unlink(missing_ok=True)
        try:
            queue.rmdir()
        except OSError:
            pass
    print(f'NotebookLM: {delivered} Dateien als zugestellt markiert; lokale Warteschlange geleert.', flush=True)
    return delivered


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    configuration = json.loads(args.config.read_text(encoding='utf-8')) if args.config else None
    sys.exit(build(args.root, configuration))
