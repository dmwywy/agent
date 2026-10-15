@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 未检测到 Python，正在打开官方下载页……
  start "" https://www.python.org/downloads/windows/
  echo 安装时请勾选 "Add Python to PATH"，装好后重新双击本文件。
  echo.
  pause
  exit /b 1
)

python 启动器.py
pause
