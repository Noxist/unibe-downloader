"""Create incremental PDF companions without changing downloaded originals."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from sync_support import file_hash

OFFICE = {'.doc', '.docx', '.docm', '.dot', '.dotx', '.odt', '.ott', '.rtf',
          '.ppt', '.pptx', '.pptm', '.pps', '.ppsx', '.odp', '.otp',
          '.xls', '.xlsx', '.xlsm', '.ods', '.ots', '.csv', '.tsv',
          '.txt', '.html', '.htm', '.odg', '.svg', '.md'}
IMAGES = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif', '.webp'}


def find_office():
    candidates = [shutil.which('soffice'), shutil.which('libreoffice')]
    for env in ('PROGRAMFILES', 'PROGRAMFILES(X86)'):
        base = os.environ.get(env)
        if base:
            candidates.extend(str(Path(base) / 'LibreOffice/program' / name)
                              for name in ('soffice.com', 'soffice.exe'))
    candidates.append('/Applications/LibreOffice.app/Contents/MacOS/soffice')
    return next((str(Path(path).resolve()) for path in candidates if path and Path(path).is_file()), None)


def validate_pdf(path):
    from pypdf import PdfReader
    with path.open('rb') as handle:
        reader = PdfReader(handle)
        if reader.is_encrypted or not len(reader.pages):
            raise ValueError('PDF ist verschluesselt oder enthaelt keine Seiten')
        # Force parsing of each page, not just the file header.
        for page in reader.pages:
            _ = page.mediabox


def convert_document(source, target, work):
    if source.suffix.lower() in IMAGES:
        from PIL import Image, ImageOps, ImageSequence
        with Image.open(source) as original:
            pages = [ImageOps.exif_transpose(frame).convert('RGB')
                     for frame in ImageSequence.Iterator(original)]
            try:
                pages[0].save(target, 'PDF', save_all=True, append_images=pages[1:])
            finally:
                for page in pages:
                    page.close()
        return
    office = find_office()
    if not office:
        raise RuntimeError('LibreOffice fehlt. Bitte LibreOffice installieren und erneut starten.')
    profile = work / 'profile'
    (profile / 'user').mkdir(parents=True)
    # Isolated non-interactive profile: don't run document macros or update links.
    (profile / 'user/registrymodifications.xcu').write_text('''<?xml version="1.0"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
</oor:items>''', encoding='utf-8')
    suffix = '.txt' if source.suffix.lower() == '.md' else source.suffix.lower()
    local_source = work / ('source' + suffix)
    shutil.copyfile(source, local_source)
    output = work / 'output'
    output.mkdir()
    command = [office, '-env:UserInstallation=' + profile.as_uri(), '--headless',
               '--nologo', '--nodefault', '--norestore', '--convert-to', 'pdf',
               '--outdir', str(output), str(local_source)]
    result = subprocess.run(command, capture_output=True, timeout=180,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    generated = output / 'source.pdf'
    if result.returncode or not generated.is_file():
        raise RuntimeError(f'LibreOffice hat kein PDF erzeugt (Exit {result.returncode}).')
    shutil.copyfile(generated, target)


def convert_tree(root, converter=convert_document, only_folders=None):
    root = Path(root).resolve()
    stats = {'converted': 0, 'unchanged': 0, 'unsupported': 0, 'errors': 0}
    if not root.exists():
        return stats
    index_path = root / '.pdf-index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    sources = [p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
               and (only_folders is None or p.relative_to(root).parts[0] in only_folders)
               and '.pdf-work' not in p.relative_to(root).parts
               and '.auth' not in p.relative_to(root).parts
               and not p.resolve().is_relative_to(Path(__file__).resolve().parent)
               and not p.name.startswith('.') and not p.name.endswith('.part')
               and p.suffix.lower() not in {'.pdf', '.epub', '.mp4', '.mkv', '.webm', '.m4v', '.mp3', '.m4a', '.wav', '.ts'}]
    for source in sources:
        if not source.resolve().is_relative_to(root):
            continue
        if source.suffix.lower() not in OFFICE | IMAGES:
            stats['unsupported'] += 1
            print(f'  PDF nicht unterstuetzt: {source.relative_to(root)}')
            continue
        key = source.relative_to(root).as_posix()
        try:
            digest = file_hash(source)
            previous = index.get(key, {})
            target = source.with_suffix('.pdf')
            number = 1
            while target.exists() and previous.get('pdf_path') != target.relative_to(root).as_posix():
                suffix = ' (konvertiert)' if number == 1 else f' (konvertiert {number})'
                target = source.with_name(source.stem + suffix + '.pdf')
                number += 1
            legacy = source.with_name(source.name + '.pdf')
            if (not target.exists() and legacy.is_file() and not legacy.is_symlink()
                    and previous.get('source_sha256') == digest
                    and previous.get('pdf_sha256') == file_hash(legacy)):
                legacy.rename(target)
                previous['pdf_path'] = target.relative_to(root).as_posix()
                index[key] = previous
                temporary = index_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding='utf-8')
                temporary.replace(index_path)
                print(f'  PDF umbenannt: {target.relative_to(root)}')
            if (target.is_file() and not target.is_symlink()
                    and previous.get('source_sha256') == digest
                    and previous.get('pdf_sha256') == file_hash(target)):
                stats['unchanged'] += 1
                continue
            if target.is_symlink():
                raise ValueError('PDF-Ziel ist eine Verknuepfung')
            workspace = root / '.pdf-work'
            workspace.mkdir(exist_ok=True)
            if not workspace.resolve().is_relative_to(root):
                raise ValueError('Temporaerer Ordner liegt ausserhalb des Downloadordners')
            with tempfile.TemporaryDirectory(dir=workspace) as temp:
                work = Path(temp).resolve()
                if not work.is_relative_to(root):
                    raise ValueError('Ungueltiger temporaerer Pfad')
                generated = work / 'converted.pdf'
                converter(source, generated, work)
                validate_pdf(generated)
                if file_hash(source) != digest:
                    raise RuntimeError('Original wurde waehrend der Konvertierung geaendert; naechster Lauf versucht es erneut.')
                if target.exists():
                    archive = target.parent / '.versions' / file_hash(target) / target.name
                    if not archive.resolve().is_relative_to(root):
                        raise ValueError('Archivpfad liegt ausserhalb des Downloadordners')
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, archive)
                # Work and target share a filesystem; replace atomically after validation.
                generated.replace(target)
            index[key] = {'source_sha256': digest, 'pdf_sha256': file_hash(target),
                          'pdf_path': target.relative_to(root).as_posix()}
            temporary = index_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding='utf-8')
            temporary.replace(index_path)
            stats['converted'] += 1
            print(f'  PDF erstellt: {target.relative_to(root)}')
        except Exception as exc:
            stats['errors'] += 1
            print(f'  PDF-FEHLER {source.relative_to(root)}: {exc}')
    print(f"PDF-Pruefung: {stats['converted']} erstellt, {stats['unchanged']} aktuell, "
          f"{stats['unsupported']} nicht unterstuetzt, {stats['errors']} Fehler.")
    return stats
