"""Linux runner: existing edu-ID automation -> downloads -> non-destructive rclone copy."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent


def release_uploaded_lectures(staging, target, drive_root_folder_id=''):
    """Free local lecture media only after rclone verifies every source file."""
    from lecture_archive import remove_verified_files, verify_drive_copy

    index_path = staging / '.podcast-index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    removed_count = 0
    for course_folder in sorted(path for path in staging.iterdir() if path.is_dir()):
        for year_folder in sorted(path for path in course_folder.glob('20[0-9][0-9]') if path.is_dir()):
            lectures = year_folder / 'Lectures'
            if not lectures.is_dir():
                continue
            remote = f"{target.rstrip('/')}/{course_folder.name}/{year_folder.name}/Lectures"
            try:
                verified = verify_drive_copy(lectures, remote, drive_root_folder_id)
            except (OSError, subprocess.CalledProcessError) as exc:
                detail = f'rclone Exitcode {exc.returncode}' if isinstance(exc, subprocess.CalledProcessError) else type(exc).__name__
                print(f'  [Speicher] {course_folder.name}/{year_folder.name}: Drive-Prüfung fehlgeschlagen; lokal behalten ({detail})', flush=True)
                continue
            if not verified:
                continue

            verified_mp4 = {str(path.relative_to(staging)) for path in verified
                            if path.suffix.lower() == '.mp4'}
            changed_index = False
            for relative_path in verified_mp4:
                found_record = False
                for record in index.values():
                    if record.get('relative_path') == relative_path:
                        found_record = True
                        record['drive_uploaded'] = True
                        record['drive_uploaded_at'] = datetime.now(timezone.utc).isoformat()
                        record['status'] = 'uploaded'
                        changed_index = True
                if not found_record:
                    # Also prevent an unindexed but verified Opencast path from
                    # being fetched again after its local media file is removed.
                    index['drive-uploaded:' + relative_path] = {
                        'relative_path': relative_path,
                        'drive_uploaded': True,
                        'drive_uploaded_at': datetime.now(timezone.utc).isoformat(),
                    }
                    changed_index = True
            if changed_index:
                temporary = index_path.with_suffix(index_path.suffix + '.tmp')
                temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
                temporary.replace(index_path)

            removed = remove_verified_files(verified)
            removed_count += len(removed)
            if removed:
                print(f'  [Speicher] {course_folder.name}/{year_folder.name}: {len(removed)} verifizierte Lecture-Datei(en) lokal entfernt.', flush=True)
    return removed_count


def execute(config_path):
    import fcntl
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    target = config.get('drive_remote', '')
    if ':' not in target or not target.split(':', 1)[1].strip('/'):
        raise ValueError('drive_remote muss auf einen konkreten Drive-Unterordner zeigen.')
    staging = Path(config['output'])
    state = Path(config['state_file'])
    if not staging.is_absolute() or not state.is_absolute():
        raise ValueError('Server: output und state_file müssen absolute lokale Pfade sein.')
    auth = config.get('auth_command')
    if not isinstance(auth, list) or not auth or not all(isinstance(x, str) for x in auth):
        raise ValueError('auth_command muss die bestehende edu-ID-Automation als Argumentliste enthalten.')
    lock = config_path.with_suffix('.lock')
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Server-Synchronisation läuft bereits.')
        print('1/3 edu-ID-Sitzung aktualisieren …', flush=True)
        subprocess.run(auth, cwd=config.get('auth_cwd', str(config_path.parent)),
                       env=dict(os.environ, ILIAS_STORAGE_STATE=str(state)), check=True,
                       timeout=config.get('auth_timeout_seconds', 300))
        if not state.is_file():
            raise RuntimeError('Die Anmeldeautomation hat keine Playwright-Sitzungsdatei bereitgestellt.')
        print('2/3 ILIAS-Dateien und Podcasts herunterladen …', flush=True)
        result = subprocess.run([sys.executable, str(ROOT / 'app/downloader.py'), 'sync',
                                 '--config', str(config_path), '--downloads-only', '--state', str(state)])
        print('2b/3 Veröffentlichte Podcasts herunterladen …', flush=True)
        from playwright.sync_api import sync_playwright
        from podcast_sync import sync_podcasts
        with sync_playwright() as playwright:
            podcast_result = sync_podcasts(playwright, config, state)
        print('2c/3 Lectures transkribieren …', flush=True)
        from transcribe import transcribe_tree
        transcript_result = transcribe_tree(staging, config.get('transcription_model', 'small'),
                                             config.get('transcription_language', 'de'))
        print('2d/3 NotebookLM-Ordner aktualisieren …', flush=True)
        from notebooklm_export import build as build_notebooklm
        notebooklm_result = build_notebooklm(staging, config)
        if not staging.is_dir():
            raise RuntimeError('Kein Download-Ordner vorhanden; Drive-Übertragung ausgelassen.')
        print('3/3 Neue/geänderte Dateien nach Google Drive kopieren …', flush=True)
        # copy never deletes destination-only documents/PDF companions from the laptop.
        command = ['rclone', 'copy', str(staging), target,
            '--exclude', '.*', '--exclude', '**/.*', '--exclude', '**/*.part',
            '--exclude', '**/*.tmp', '--exclude', '**/NotebookLM/**',
            '--create-empty-src-dirs', '--stats', '30s']
        root_id = config.get('drive_root_folder_id', '').strip()
        if root_id:
            command.extend(['--drive-root-folder-id', root_id])
        copied = subprocess.run(command)
        if copied.returncode == 0:
            print('  Hochgeladene Lectures auf Drive prüfen und lokale Medien freigeben …', flush=True)
            release_uploaded_lectures(staging, target, root_id)
        notebooklm_copy_error = 0
        # NotebookLM is a one-way inbox. Copy new queue entries, never sync:
        # files the user removes after importing must stay removed.
        notebooklm_target = config.get('notebooklm_drive_remote', target).rstrip('/')
        notebooklm_root_id = config.get('notebooklm_drive_root_folder_id', root_id).strip()
        course_names = {course['name'] + '__' + str(course['ref_id']): course['name']
                        for course in config.get('courses', [])}
        for course_folder in sorted(path for path in staging.iterdir() if path.is_dir()):
            source = course_folder / 'NotebookLM'
            if not source.is_dir():
                continue
            # Only create a subject folder in NotebookLM on Drive if there are actual new files!
            files_to_copy = [p for p in source.iterdir() if p.is_file() and not p.name.startswith('.') and not p.name.endswith('.tmp')]
            if not files_to_copy:
                continue
            subject = course_names.get(course_folder.name, course_folder.name)
            destination = f"{notebooklm_target}/{subject}"
            print(f'  [NotebookLM] {len(files_to_copy)} neue Datei(en) für {subject} nach Drive übertragen …', flush=True)
            sync_command = ['rclone', 'copy', str(source), destination,
                            '--exclude', '.*', '--exclude', '*.tmp']
            if notebooklm_root_id:
                sync_command.extend(['--drive-root-folder-id', notebooklm_root_id])
            notebooklm_copy_error |= subprocess.run(sync_command).returncode
        if not notebooklm_copy_error:
            from notebooklm_export import acknowledge
            acknowledge(staging)
        return 1 if (result.returncode or podcast_result or transcript_result
                     or notebooklm_result or copied.returncode or notebooklm_copy_error) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        sys.exit(execute(args.config))
    except Exception as exc:
        print(f'Server-Synchronisation fehlgeschlagen: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
