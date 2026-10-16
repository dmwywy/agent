@echo off
rem ---------------------------------------------------------------
rem  Rebuild launcher.exe with PyInstaller (needs Python 3.10+ on PATH).
rem  Keep this file ASCII-only, see run.bat for the reason.
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

python -m pip install --quiet --upgrade pyinstaller
python -m PyInstaller --onefile --noconfirm --clean --name launcher ^
  --distpath . --workpath build --specpath build launcher.py

echo.
echo Done. launcher.exe is in this folder; the build\ folder can be deleted.
pause
