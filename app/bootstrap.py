"""Rebuild a missing/moved environment, then launch the desktop interface."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / 'app/.venv'
python = VENV / 'Scripts/python.exe'
if not python.exists():
    subprocess.run([sys.executable, '-m', 'venv', str(VENV)], check=True)
smoke = subprocess.run([str(python), '-c', 'pass'], capture_output=True)
if smoke.returncode:
    import venv
    venv.EnvBuilder(with_pip=True).create(VENV)
try:
    check = subprocess.run([str(python), '-c', 'import PySide6.QtWidgets, playwright, bs4, pypdf, PIL, paramiko'], capture_output=True)
except OSError:
    import venv
    venv.EnvBuilder(with_pip=True).create(VENV)
    check = subprocess.run([str(python), '-c', 'import PySide6.QtWidgets, playwright, bs4, pypdf, PIL, paramiko'], capture_output=True)
if check.returncode:
    subprocess.run([str(python), '-m', 'pip', 'install', '--force-reinstall', '-r', str(ROOT / 'app/requirements.txt')], check=True)
subprocess.run([str(python), '-m', 'playwright', 'install', 'chromium'], check=True)
subprocess.Popen([str(VENV / 'Scripts/pythonw.exe'), str(ROOT / 'app/gui.py')],
                 cwd=ROOT, env=dict(os.environ, PYTHONUTF8='1'), creationflags=subprocess.CREATE_NO_WINDOW)
