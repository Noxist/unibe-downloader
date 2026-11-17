"""Incrementally transcribe lecture media with faster-whisper."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def media_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def vtt_time(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}'


def atomic_text(path, content):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(content, encoding='utf-8')
    temporary.replace(path)


def transcribe_one(model, media, language='de', model_name='small'):
    base = media.with_suffix('')
    text_path = base.with_name(base.name + '.transcript.txt')
    vtt_path = base.with_name(base.name + '.transcript.vtt')
    json_path = base.with_name(base.name + '.transcript.json')
    digest = media_hash(media)
    if json_path.is_file():
        try:
            current = json.loads(json_path.read_text(encoding='utf-8'))
            if current.get('media_sha256') == digest and text_path.is_file() and vtt_path.is_file():
                return 'unverändert'
        except (OSError, ValueError):
            pass
    segments_iter, info = model.transcribe(
        str(media), language=language, beam_size=1, best_of=1, vad_filter=True,
        condition_on_previous_text=True)
    segments = []
    for segment in segments_iter:
        text = segment.text.strip()
        if text:
            segments.append({'start': segment.start, 'end': segment.end, 'text': text})
    if not segments:
        raise RuntimeError('Keine Sprache erkannt.')
    plain = '\n'.join(item['text'] for item in segments) + '\n'
    vtt = ['WEBVTT', '']
    for item in segments:
        vtt.extend([f"{vtt_time(item['start'])} --> {vtt_time(item['end'])}", item['text'], ''])
    metadata = {
        'media': media.name, 'media_sha256': digest,
        'model': model_name,
        'language': info.language, 'language_probability': info.language_probability,
        'duration_seconds': info.duration, 'segments': segments,
    }
    atomic_text(text_path, plain)
    atomic_text(vtt_path, '\n'.join(vtt))
    atomic_text(json_path, json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    return 'erstellt'


def transcribe_tree(root, model_name='small', language='de'):
    from faster_whisper import WhisperModel
    media = sorted(Path(root).glob('*/20[0-9][0-9]/Lectures/*.mp4'))
    pending = []
    for path in media:
        metadata = path.with_suffix('').with_name(path.stem + '.transcript.json')
        if not metadata.is_file():
            pending.append(path)
            continue
        try:
            if json.loads(metadata.read_text(encoding='utf-8')).get('media_sha256') != media_hash(path):
                pending.append(path)
        except (OSError, ValueError):
            pending.append(path)
    if not pending:
        print(f'Transkripte: {len(media)} unverändert, 0 offen.', flush=True)
        return 0
    print(f'Transkripte: Modell {model_name}, {len(pending)} von {len(media)} Lectures offen.', flush=True)
    model = WhisperModel(model_name, device='cpu', compute_type='int8', cpu_threads=6)
    errors = 0
    for number, path in enumerate(pending, 1):
        try:
            status = transcribe_one(model, path, language, model_name)
            print(f'  [{number}/{len(pending)}] {status}: {path.name}', flush=True)
        except Exception as exc:
            errors += 1
            print(f'  FEHLER {path.name}: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
    return 1 if errors else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--model', default='small')
    parser.add_argument('--language', default='de')
    args = parser.parse_args()
    sys.exit(transcribe_tree(args.root, args.model, args.language))
