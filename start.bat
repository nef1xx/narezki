@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9+ is required. Install from https://www.python.org/downloads/
  pause
  exit /b 1
)
python app.py --open
if errorlevel 1 pause
