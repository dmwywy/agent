@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  一键复现：从上到下依次执行，双击即可
REM  运行时会让你输入材料文件夹路径；直接回车则用仓库自带 demo 数据
REM ============================================================
set "RUNID=reproduce"
set "MODE=--offline"
REM 想在线运行：把上面这行改成  set "MODE="  （需先配好 .env 中的 DEEPSEEK_API_KEY）

echo.
echo ==== 第 0 步：输入材料文件夹路径 ====
echo   文件夹里可混放年报 / 研究报告的 PDF、DOCX（支持多份）。
set /p "FROM=请输入路径，直接回车 = 使用仓库自带 demo 数据："
set FROM=%FROM:"=%
if "%FROM:~-1%"=="\" set "FROM=%FROM:~0,-1%"
if defined FROM (
  echo   [OK] 已选择：%FROM%
) else (
  echo   [OK] 未输入，使用仓库自带 demo 数据
)
echo   运行模式：%MODE%

echo.
echo ==== [1/6] 检查运行环境 ====
python -V || goto :fail
python -c "import openai,pydantic,yaml,pdfplumber,reportlab,openpyxl,pandas,streamlit" 2>nul || goto :dep
echo   [OK] Python 与依赖齐备

echo.
echo ==== [2/6] 契约自检（不联网、不调模型） ====
python testcontract.py || goto :fail

echo.
echo ==== [3/6] 字段与规则库自检 ====
python tools\check_fields.py || goto :fail

echo.
echo ==== [4/6] MCP 服务自检（不联网、不调模型） ====
python -m mcp_server --selftest || goto :fail

echo.
echo ==== [5/6] 运行完整核查流程 ====
if defined FROM (
  python -m fincheck ingest --from "%FROM%" --run-id %RUNID% %MODE%
) else (
  python -m fincheck run --config configs\demo.yaml --run-id %RUNID% %MODE%
)
if errorlevel 1 goto :fail

echo.
echo ==== [6/6] 产物与报告 ====
echo   runs\%RUNID%\ 下的内容：
dir /b "runs\%RUNID%"
echo.
echo   报告位置：
echo     runs\%RUNID%\report.pdf
echo     runs\%RUNID%\report.xlsx
echo     runs\%RUNID%\report.html
echo.
echo 全部完成。
pause
exit /b 0

:dep
echo.
echo   [!!] 缺少依赖，请先执行： python -m pip install -r requirements.txt
pause
exit /b 1

:fail
echo.
echo   [!!] 上一步失败，请按上面的提示处理后重跑。
pause
exit /b 1
