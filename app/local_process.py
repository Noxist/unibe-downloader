"""Laptop workflow: Google Drive originals -> PDF companions -> reMarkable."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def process(config):
    from pdf_conversion import convert_tree
    from remarkable import sync_tree
    source = (ROOT / config['output']).resolve()
    if not source.is_dir():
        raise RuntimeError('Google-Drive-Ordner nicht verfügbar: ' + str(source))
    print('1/2 Dokumente in PDF umwandeln …', flush=True)
    result = convert_tree(source)
    if (ROOT / '.auth/stop-request').exists():
        raise KeyboardInterrupt
    print('2/2 PDFs und EPUBs ans reMarkable senden …', flush=True)
    sent = sync_tree(source)
    return 1 if result['errors'] or sent else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'app/config.json')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    lock = ROOT / '.sync.lock'
    try:
        handle = lock.open('x')
    except FileExistsError:
        raise RuntimeError('Ein Lauf ist bereits aktiv.')
    try:
        with handle:
            (ROOT / '.auth/stop-request').unlink(missing_ok=True)
            return process(config)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Gestoppt.')
        sys.exit(130)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
