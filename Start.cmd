@echo off
cd /d "%~dp0"
py -3 app\bootstrap.py
if errorlevel 1 (
  echo Start fehlgeschlagen. Python 3.11 oder neuer muss installiert sein.
  pause
)
