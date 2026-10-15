@echo off
rem 重新打包 launcher.exe（PyInstaller 单文件外壳）。需要在项目根目录、已安装 Python 3.10+。
setlocal
cd /d "%~dp0"

python -m pip install --quiet --upgrade pyinstaller
python -m PyInstaller --onefile --noconfirm --clean --name launcher ^
  --distpath . --workpath build --specpath build launcher.py

echo.
echo 打包完成：launcher.exe（中间产物在 build\ 目录，可删除）
pause
