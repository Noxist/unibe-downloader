"""Verify lecture copies on Drive before freeing their local media files."""
from pathlib import Path
import subprocess


LECTURE_SUFFIXES = {'.mp4', '.transcript.txt', '.transcript.vtt', '.transcript.json'}


def is_lecture_artifact(path):
    name = path.name.lower()
    return (path.suffix.lower() == '.mp4'
            or any(name.endswith(suffix) for suffix in LECTURE_SUFFIXES
                   if suffix != '.mp4'))


def _identity(path):
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def verify_drive_copy(lectures, remote, drive_root_folder_id=''):
    """Return stable lecture artifact paths only after a successful hash check."""
    lectures = Path(lectures)
    candidates = [path for path in lectures.rglob('*')
                  if path.is_file() and not path.is_symlink() and is_lecture_artifact(path)]
    snapshots = {path: _identity(path) for path in candidates}
    if not snapshots:
        return []

    command = ['rclone', 'check', str(lectures), remote, '--one-way']
    if drive_root_folder_id:
        command.extend(['--drive-root-folder-id', drive_root_folder_id])
    subprocess.run(command, check=True)

    # Do not remove files that changed while the remote comparison was running.
    return [path for path, identity in snapshots.items()
            if path.exists() and not path.is_symlink() and _identity(path) == identity]


def remove_verified_files(paths):
    """Remove only the checked lecture files, keeping indexes and directories."""
    removed = []
    for path in paths:
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            continue
    return removed
