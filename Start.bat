@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if not errorlevel 1 (
  py -3 app.py
  if errorlevel 1 pause
  exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
  python app.py
  if errorlevel 1 pause
  exit /b
)
echo Install Python 3.11 or newer from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH" and install Tcl/Tk, then double-click Start.bat again.
pause
