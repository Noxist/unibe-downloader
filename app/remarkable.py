"""Standalone USB/WLAN SSH setup and incremental reMarkable library import."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

import paramiko
from sync_support import file_hash

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ROOT / '.auth/remarkable'
USB = '10.11.99.1'
LIBRARY = '/home/root/.local/share/remarkable/xochitl'


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def run(client, command):
    _, stdout, stderr = client.exec_command(command, timeout=90)
    output = stdout.read().decode('utf-8', errors='replace')
    error = stderr.read().decode('utf-8', errors='replace')
    if stdout.channel.recv_exit_status():
        raise RuntimeError(error.strip() or 'Tablet-Befehl fehlgeschlagen.')
    return output.strip()


def settings():
    if not (PRIVATE / 'connection.json').exists():
        raise RuntimeError('Bitte zuerst „reMarkable einrichten“ ausführen.')
    return json.loads((PRIVATE / 'connection.json').read_text(encoding='utf-8'))


def connect(host, username, password=None, first_usb=False):
    client = paramiko.SSHClient()
    hosts = PRIVATE / 'known_hosts'
    if hosts.exists():
        client.load_host_keys(str(hosts))
    # Initial physical USB pairing; later connections always check the saved key.
    if first_usb and host == USB:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=username, password=password,
                       key_filename=str(PRIVATE / 'id_rsa') if password is None else None,
                       look_for_keys=False, allow_agent=False, timeout=5,
                       banner_timeout=8, auth_timeout=8)
        client.get_transport().set_keepalive(15)
        return client
    except Exception:
        client.close()
        raise


def secure_directory():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        identity = subprocess.check_output(['whoami'], text=True).strip()
        subprocess.run(['icacls', str(PRIVATE), '/inheritance:r', '/grant:r',
                        identity + ':(OI)(CI)F'], check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        PRIVATE.chmod(0o700)


def setup(username, password, wifi=''):
    secure_directory()
    print('1/4 USB-Verbindung prüfen …', flush=True)
    client = connect(USB, username, password, first_usb=True)
    try:
        client.save_host_keys(str(PRIVATE / 'known_hosts'))
        print('2/4 SSH-Schlüssel einrichten …', flush=True)
        keypath = PRIVATE / 'id_rsa'
        if keypath.exists():
            key = paramiko.RSAKey.from_private_key_file(str(keypath))
        else:
            key = paramiko.RSAKey.generate(3072)
            key.write_private_key_file(str(keypath))
            keypath.chmod(0o600)
        public = key.get_name() + ' ' + key.get_base64() + ' ilias-downloader'
        (PRIVATE / 'id_rsa.pub').write_text(public + '\n', encoding='ascii')
        run(client, 'mkdir -p ~/.ssh && chmod 700 ~/.ssh')
        with client.open_sftp() as sftp:
            home = sftp.normalize('.')
            authorized = home + '/.ssh/authorized_keys'
            try:
                with sftp.open(authorized, 'r') as f:
                    existing = f.read().decode('utf-8')
            except FileNotFoundError:
                existing = ''
            if key.get_base64() not in existing:
                with sftp.open(authorized, 'a') as f:
                    f.write(('\n' if existing and not existing.endswith('\n') else '') + public + '\n')
            sftp.chmod(authorized, 0o600)
        print('3/4 WLAN-SSH aktivieren …', flush=True)
        run(client, 'rm-ssh-over-wlan on')
        addresses = run(client, 'ip -4 addr show wlan0')
        match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', addresses)
        wifi = wifi.strip() or (match[1] if match else '')
        if wifi:
            hostkey = client.get_transport().get_remote_server_key()
            client.get_host_keys().add(wifi, hostkey.get_name(), hostkey)
            client.save_host_keys(str(PRIVATE / 'known_hosts'))
        cfg = {'username': username, 'wifi': wifi, 'library': LIBRARY, 'folder': 'ILIAS'}
        write_json(PRIVATE / 'connection.json', cfg)
    finally:
        client.close()
    print('4/4 Anmeldung ohne Passwort prüfen …', flush=True)
    with connect(USB, username) as check:
        run(check, 'true')
    if wifi:
        try:
            with connect(wifi, username) as check:
                run(check, 'true')
            print('USB und WLAN bereit. Künftig wird der gespeicherte SSH-Schlüssel verwendet.', flush=True)
        except (OSError, paramiko.SSHException) as exc:
            print(f'USB bereit. WLAN noch nicht erreichbar ({type(exc).__name__}); USB bleibt verwendbar.', flush=True)
    return cfg


def open_device(mode='auto'):
    cfg = settings()
    hosts = [USB, cfg.get('wifi')] if mode == 'auto' else [USB if mode == 'usb' else cfg.get('wifi')]
    problems = []
    for host in dict.fromkeys(h for h in hosts if h):
        try:
            client = connect(host, cfg['username'])
            if host == USB:
                # A DHCP change is learned through the already authenticated USB link.
                addresses = run(client, 'ip -4 addr show wlan0')
                match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)/', addresses)
                if match and match[1] != cfg.get('wifi'):
                    key = client.get_transport().get_remote_server_key()
                    client.get_host_keys().add(match[1], key.get_name(), key)
                    client.save_host_keys(str(PRIVATE / 'known_hosts'))
                    cfg['wifi'] = match[1]
                    write_json(PRIVATE / 'connection.json', cfg)
            print(f'reMarkable verbunden: {"USB" if host == USB else "WLAN"} ({host})', flush=True)
            return client, cfg
        except (OSError, paramiko.SSHException) as exc:
            problems.append(f'{host}: {type(exc).__name__}')
    raise RuntimeError('reMarkable nicht erreichbar. Einschalten/USB prüfen oder WLAN-IP neu einrichten. ' + '; '.join(problems))


def collect(source, only_folders=None):
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError('Download-Ordner nicht verfügbar: ' + str(source))
    files, folders = [], set()
    for base, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(base) / d).is_symlink())
        relative = Path(base).relative_to(source)
        if relative.parts and only_folders is not None and relative.parts[0] not in only_folders:
            dirs[:] = []
            continue
        folders.add(relative)
        for name in sorted(names):
            path = Path(base) / name
            if not name.startswith('.') and not path.is_symlink() and path.suffix.lower() in ('.pdf', '.epub'):
                files.append(path)
    return source, files, sorted(folders, key=lambda p: (len(p.parts), str(p)))


def metadata(name, parent, kind):
    now = str(int(time.time() * 1000))
    return {'createdTime': now, 'lastModified': now, 'parent': parent,
            'pinned': False, 'type': kind, 'visibleName': name,
            'new': False, 'source': 'ilias-downloader'}


def content_for(path):
    if path.suffix.lower() == '.epub':
        import zipfile
        with zipfile.ZipFile(path) as book:
            if book.read('mimetype').strip() != b'application/epub+zip':
                raise ValueError('Ungültige EPUB-Datei: ' + path.name)
        return {'fileType': 'epub'}
    from pypdf import PdfReader
    with path.open('rb') as f:
        reader = PdfReader(f)
        if reader.is_encrypted or not len(reader.pages):
            raise ValueError('PDF verschlüsselt oder leer: ' + path.name)
        pages = len(reader.pages)
    # xochitl migrates this import format to its current cPages schema on opening.
    return {'fileType': 'pdf', 'pageCount': pages, 'originalPageCount': pages,
            'lastOpenedPage': 0, 'coverPageNumber': -1, 'orientation': 'portrait',
            'documentMetadata': {}, 'extraMetadata': {}, 'fontName': '',
            'lineHeight': -1, 'margins': 100, 'textScale': 1, 'textAlignment': 'justify',
            'sizeInBytes': str(path.stat().st_size)}


class Library:
    def __init__(self, client, path):
        self.client = client
        self.path = path
        self.sftp = client.open_sftp()
        self.sftp.get_channel().settimeout(90)
        self.sftp.stat(path)
        self.names = set(self.sftp.listdir(path))
        self.changed = False

    def read(self, name):
        with self.sftp.open(self.path + '/' + name) as f:
            return json.loads(f.read())

    def json(self, name, data):
        target = self.path + '/' + name
        temp = target + '.ilias-part'
        with self.sftp.open(temp, 'w') as f:
            f.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
        # Only new IDs are published; existing tablet annotations are never replaced.
        self.sftp.rename(temp, target)
        self.names.add(name)
        self.changed = True

    def digest(self, name):
        return run(self.client, 'sha256sum ' + shlex.quote(self.path + '/' + name)).split()[0]

    def folder(self, identity, name, parent):
        filename = identity + '.metadata'
        if filename in self.names:
            data = self.read(filename)
            if data.get('deleted') or data.get('parent') == 'trash':
                raise RuntimeError('Zielordner liegt im Tablet-Papierkorb: ' + name)
            if data.get('type') != 'CollectionType':
                raise RuntimeError('Ordner-ID ist bereits anderweitig belegt.')
            return
        self.json(filename, metadata(name, parent, 'CollectionType'))

    def upload(self, identity, source, parent, title, digest):
        filename = identity + source.suffix.lower()
        if identity + '.metadata' in self.names:
            existing = self.read(identity + '.metadata')
            if existing.get('deleted') or existing.get('parent') == 'trash':
                raise RuntimeError('Dokument liegt im Tablet-Papierkorb: ' + title)
            if existing.get('type') != 'DocumentType' or self.digest(filename) != digest:
                raise RuntimeError('Bestehende Tablet-Datei weicht ab; wird nicht überschrieben: ' + title)
            return False
        content = content_for(source)
        target = self.path + '/' + filename
        temp = target + '.ilias-part'
        self.sftp.put(str(source), temp)
        if self.digest(filename + '.ilias-part') != digest:
            raise RuntimeError('Prüfsumme stimmt nach Upload nicht überein: ' + source.name)
        if filename in self.names:
            if self.digest(filename) != digest:
                raise RuntimeError('Unvollständiger früherer Import weicht ab: ' + source.name)
            self.sftp.remove(temp)
        else:
            self.sftp.rename(temp, target)
            self.names.add(filename)
        if identity + '.content' not in self.names:
            self.json(identity + '.content', content)
        if identity + '.pagedata' not in self.names:
            with self.sftp.open(self.path + '/' + identity + '.pagedata', 'w') as f:
                f.write(('Blank\n' * content.get('pageCount', 0)).encode('utf-8'))
            self.names.add(identity + '.pagedata')
        self.json(identity + '.metadata', metadata(title, parent, 'DocumentType'))
        return True


def prepare_document(path, work):
    """Preserve the original; strip only encryption readable with an empty password."""
    if path.suffix.lower() != '.pdf':
        return path
    from pypdf import PdfReader, PdfWriter
    with path.open('rb') as f:
        reader = PdfReader(f)
        if not reader.is_encrypted:
            return path
        if not reader.decrypt(''):
            raise ValueError('PDF benötigt ein Lesepasswort: ' + path.name)
        target = work / 'readable.pdf'
        writer = PdfWriter(clone_from=reader)
        writer.write(target)
        print('  PDF für Tablet vorbereitet (Original bleibt unverändert).', flush=True)
        return target


def sync_tree(source, only_folders=None, transport='auto'):
    write_json(PRIVATE / 'last-transfer.json', {'status': 'running', 'time': time.strftime('%Y-%m-%dT%H:%M:%S')})
    try:
        return _sync_tree(source, only_folders, transport)
    except BaseException as exc:
        write_json(PRIVATE / 'last-transfer.json', {
            'status': 'stopped' if isinstance(exc, KeyboardInterrupt) else 'failed',
            'error': type(exc).__name__, 'time': time.strftime('%Y-%m-%dT%H:%M:%S')})
        raise


def _sync_tree(source, only_folders=None, transport='auto'):
    source, files, folders = collect(source, only_folders)
    if not files:
        write_json(PRIVATE / 'last-transfer.json', {'status': 'empty', 'sent': 0})
        print('reMarkable: Keine PDF-/EPUB-Dateien gefunden.', flush=True)
        return 0
    client, cfg = open_device(transport)
    # Persist beside local auth, never inside the cloud-synchronised downloads.
    index_path = PRIVATE / 'transfer-index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'sources': {}}
    source_key = source.name
    state = index['sources'].setdefault(source_key, {'namespace': str(uuid.uuid4()), 'files': {}})
    write_json(index_path, index)  # stable IDs even if the connection is interrupted
    namespace = uuid.UUID(state['namespace'])
    identity = lambda key: str(uuid.uuid5(namespace, key))
    count = unchanged = errors = 0
    library = None
    locked = False
    started = time.monotonic()
    try:
        run(client, 'mkdir /tmp/ilias-import.lock')
        locked = True
        library = Library(client, cfg['library'])
        main_id = identity('ILIAS')
        library.folder(main_id, cfg.get('folder', 'ILIAS'), '')
        for folder in folders:
            key = folder.as_posix()
            parent = main_id if not folder.parts else identity('folder:' + folder.parent.as_posix())
            title = source.name if not folder.parts else re.sub(r'__\d+$', '', folder.name)
            library.folder(identity('folder:' + key), title, parent)
        for number, path in enumerate(files, 1):
            if (ROOT / '.auth/stop-request').exists():
                raise KeyboardInterrupt
            relative = path.relative_to(source).as_posix()
            try:
                digest = file_hash(path)
                document = identity('document:' + relative + ':' + digest)
                previous = state['files'].get(relative)
                title = re.sub(r'^\d+__', '', path.stem)
                if previous and previous['sha256'] != digest:
                    title += ' (Stand ' + time.strftime('%d.%m.%Y %H:%M') + ')'
                parent = identity('folder:' + path.relative_to(source).parent.as_posix())
                if previous and previous['sha256'] == digest and document + '.metadata' in library.names:
                    payload_digest = previous.get('remote_sha256', digest)
                    uploaded = library.upload(document, path, parent, title, payload_digest)
                else:
                    with tempfile.TemporaryDirectory(prefix='transfer-', dir=PRIVATE) as temp:
                        payload = prepare_document(path, Path(temp))
                        payload_digest = file_hash(payload)
                        uploaded = library.upload(document, payload, parent, title, payload_digest)
                state['files'][relative] = {'uuid': document, 'sha256': digest, 'remote_sha256': payload_digest}
                write_json(index_path, index)
                count += uploaded
                unchanged += not uploaded
                print(f'  [reMarkable {number}/{len(files)}] {"Gesendet" if uploaded else "Aktuell"}: {relative}', flush=True)
            except (OSError, ValueError, RuntimeError) as exc:
                errors += 1
                print(f'  [reMarkable FEHLER] {relative}: {exc}', flush=True)
    finally:
        try:
            if library:
                library.sftp.close()
                if library.changed:
                    print('reMarkable-Bibliothek neu laden …', flush=True)
                    run(client, 'systemctl restart xochitl')
                    for _ in range(20):
                        if run(client, 'systemctl is-active xochitl || true') == 'active':
                            break
                        time.sleep(1)
                    else:
                        raise RuntimeError('reMarkable-Oberfläche startet nicht; bitte Tablet prüfen.')
        finally:
            try:
                if locked:
                    run(client, 'rmdir /tmp/ilias-import.lock')
            finally:
                client.close()
    summary = {'status': 'failed' if errors else 'complete', 'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'sent': count,
               'unchanged': unchanged, 'errors': errors, 'seconds': round(time.monotonic() - started)}
    write_json(PRIVATE / 'last-transfer.json', summary)
    print(f'reMarkable: {count} gesendet, {unchanged} aktuell, {errors} Fehler.', flush=True)
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['setup', 'sync', 'status'])
    parser.add_argument('--setup-stdin', action='store_true')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--transport', choices=['auto', 'usb', 'wifi'], default='auto')
    args = parser.parse_args()
    lock = ROOT / '.sync.lock'
    try:
        handle = lock.open('x')
    except FileExistsError:
        raise RuntimeError('Ein Lauf ist bereits aktiv.')
    try:
        with handle:
            if args.command == 'setup':
                if args.setup_stdin:
                    data = json.loads(sys.stdin.readline())
                else:
                    print('Tablet per USB anschliessen. Passwort steht unter Hilfe → Copyrights und Lizenzen.')
                    data = {'username': input('Benutzername [root]: ').strip() or 'root',
                            'password': getpass.getpass('SSH-Passwort: '),
                            'wifi': input('WLAN-IP [automatisch erkennen]: ').strip()}
                setup(**data)
            elif args.command == 'status':
                client, _ = open_device(args.transport)
                with client:
                    print(run(client, 'cat /usr/share/remarkable/update.conf; systemctl is-active xochitl'))
            else:
                source = args.source
                if source is None:
                    cfg = json.loads((ROOT / 'app/config.json').read_text(encoding='utf-8-sig'))
                    source = ROOT / cfg['output']
                (ROOT / '.auth/stop-request').unlink(missing_ok=True)
                return sync_tree(source, transport=args.transport)
    finally:
        lock.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Gestoppt.')
        sys.exit(130)
    except Exception as exc:
        print(f'reMarkable: {exc}', file=sys.stderr)
        sys.exit(1)
