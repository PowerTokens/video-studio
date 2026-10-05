@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if errorlevel 1 (
  set "PT_PYTHON=python"
) else (
  set "PT_PYTHON=py -3"
)
%PT_PYTHON% -m venv .build-venv
if errorlevel 1 goto fail
.build-venv\Scripts\python.exe -m pip install "pyinstaller>=6,<7" -r requirements.txt
if errorlevel 1 goto fail
.build-venv\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 goto fail
REM Bundle imageio-ffmpeg package data (includes platform ffmpeg binary,
REM ~70–80 MB on Windows x86_64). See BUILD-NOTES.md.
.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name PowerTokensVideoStudio --icon assets\logo.ico --add-data "assets;assets" --add-data "*.xlsx;." --collect-all imageio_ffmpeg app.py
if errorlevel 1 goto fail
echo Done: dist\PowerTokensVideoStudio.exe
pause
exit /b 0
:fail
echo Build failed. Please keep this window and inspect the error above.
pause
exit /b 1
