# -*- coding: utf-8 -*-
"""交互式启动器：环境自检 → 选择命令行 / 网页 → 检查 DeepSeek 配置 → 输入材料文件夹 → 运行。

三种入口等价：
    双击仓库根目录的 `run.bat`（调用本脚本）
    或在项目根目录执行：python launcher.py
    或双击 `launcher.exe`（PyInstaller 打包的同名外壳；exe 只做引导，核查仍用旁边的源码跑）

被冻结成 exe 时（sys.frozen），本进程内部没有 Python 解释器，因此必须自行定位一个
可用的解释器；本机完全没有 Python 时可自动下载官方内嵌运行时到 `.runtime/`。
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# exe 与源码版都以「文件所在目录」为项目根：exe 需与 fincheck/ src/ app.py 放在一起
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent

REQ = ROOT / "requirements.txt"
ENV_FILE = ROOT / ".env"
RUNTIME = ROOT / ".runtime"          # 自动下载的内嵌 Python 落在这里
RUN_ID = "reproduce"

# 依赖导入名（requirements.txt 里对应的包：openai / pydantic / PyYAML /
# pdfplumber / reportlab / openpyxl / pandas / streamlit）
NEEDED = ["openai", "pydantic", "yaml", "pdfplumber",
          "reportlab", "openpyxl", "pandas", "streamlit"]

PY_MIN = (3, 10)
EMBED_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
DOWNLOAD_PAGE = "https://www.python.org/downloads/windows/"

PY: list[str] = []                   # 解析出的解释器命令，如 ["python"] 或 ["C:/.../python.exe"]


def line(text: str = "") -> None:
    print(text, flush=True)


def step(title: str) -> None:
    line()
    line("=" * 56)
    line(title)
    line("=" * 56)


def ask(prompt: str, default: str = "") -> str:
    """读取输入；管道输入到末尾（EOF）时回落到默认值，便于无人值守测试。"""
    try:
        got = input(prompt).strip()
    except EOFError:
        got = ""
    return got or default


def clean_path(raw: str) -> str:
    """去掉拖拽文件夹带来的成对引号与末尾斜杠。"""
    return raw.strip().strip('"').strip("'").rstrip("\\/")


def mask(key: str) -> str:
    return key[:6] + "***" + key[-3:] if len(key) > 12 else "***"


def _run(cmd: list[str], **kw) -> int:
    # 先把自身缓冲刷出去，否则子进程输出会插到提示语前面（管道/重定向时尤其明显）
    sys.stdout.flush()
    return subprocess.call(cmd, cwd=ROOT, **kw)


def _usable(cmd: list[str]) -> bool:
    """解释器是否可用且版本达标。"""
    try:
        probe = subprocess.run(
            cmd + ["-c", f"import sys;assert sys.version_info[:2]>={PY_MIN}"],
            capture_output=True, timeout=60)
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _version(cmd: list[str]) -> str:
    try:
        probe = subprocess.run(cmd + ["-c", "import sys;print(sys.version.split()[0])"],
                               capture_output=True, text=True, timeout=60)
        return probe.stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


# ---------------------------------------------------------------- 0. 定位 / 下载解释器

def find_python() -> list[str] | None:
    """按 内置运行时 → PATH → py 启动器 → 常见安装路径 → 项目 .venv 的顺序探测。"""
    candidates: list[list[str]] = []
    embedded = RUNTIME / "python.exe"
    if embedded.exists():
        candidates.append([str(embedded)])
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append([found])
    if shutil.which("py"):
        candidates.append(["py", "-3"])
    for pattern in (r"C:\Python3*\python.exe",
                    r"C:\Program Files\Python3*\python.exe",
                    os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Dummy"),
                                 "Programs", "Python", "Python3*", "python.exe")):
        for hit in sorted(glob.glob(pattern), reverse=True):
            candidates.append([hit])
    for parent in (ROOT, ROOT.parent):
        for rel in (Path(".venv") / "Scripts" / "python.exe", Path("venv") / "Scripts" / "python.exe"):
            p = parent / rel
            if p.exists():
                candidates.append([str(p)])

    for cmd in candidates:
        if _usable(cmd):
            return cmd
    return None


def download_runtime() -> list[str] | None:
    """下载官方内嵌 Python 到 `.runtime/`，并装好 pip（无需管理员权限、不改系统环境）。"""
    RUNTIME.mkdir(parents=True, exist_ok=True)
    zip_path = RUNTIME / "python-embed.zip"
    line(f"  正在下载官方内嵌 Python 运行时（约 11 MB）……")
    try:
        urllib.request.urlretrieve(EMBED_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(RUNTIME)
        zip_path.unlink()
    except Exception as e:                                   # noqa: BLE001
        line(f"  [!!] 下载或解压失败：{type(e).__name__}: {e}")
        return None

    # 内嵌版默认不给 pip：打开 ._pth 里的 import site，再跑 get-pip.py
    for pth in RUNTIME.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8")
        pth.write_text(text.replace("#import site", "import site"), encoding="utf-8")
    getpip = RUNTIME / "get-pip.py"
    line("  正在安装 pip……")
    try:
        urllib.request.urlretrieve(GETPIP_URL, getpip)
        if _run([str(RUNTIME / "python.exe"), str(getpip)]) != 0:
            line("  [!!] pip 安装失败")
            return None
    except Exception as e:                                   # noqa: BLE001
        line(f"  [!!] pip 下载或安装失败：{type(e).__name__}: {e}")
        return None
    finally:
        getpip.unlink(missing_ok=True)

    cmd = [str(RUNTIME / "python.exe")]
    return cmd if _usable(cmd) else None


# ---------------------------------------------------------------- 1. 环境自检

def check_python() -> list[str] | None:
    step("[1/4] 环境自检 · Python 解释器")
    cmd = find_python()
    if cmd:
        line(f"  [OK] 已找到 Python {_version(cmd)}（{cmd[0]}）")
        return cmd

    line("  [!!] 本机未检测到可用的 Python（需 3.10+）。")
    line("  1) 自动下载官方内嵌运行时到项目 .runtime/（约 11 MB，不改系统环境）")
    line("  2) 打开官方下载页，自己安装（记得勾选 “Add Python to PATH”）")
    if ask("  请输入 1 或 2（默认 1）：", "1") == "2":
        print(f"  下载页：{DOWNLOAD_PAGE}")
        if sys.platform.startswith("win"):
            try:
                os.startfile(DOWNLOAD_PAGE)                 # noqa: S606
            except OSError:
                pass
        return None
    cmd = download_runtime()
    if cmd:
        line(f"  [OK] 运行时就绪：{cmd[0]}")
    return cmd


def check_deps(cmd: list[str]) -> bool:
    step("[1/4] 环境自检 · 依赖包")
    line(f"  Python  {_version(cmd)}  ({cmd[0]})")
    missing = []
    for mod in NEEDED:
        probe = subprocess.run(cmd + ["-c", f"import {mod}"], capture_output=True)
        if probe.returncode != 0:
            missing.append(mod)

    if not missing:
        line("  [OK] 依赖齐备，无需安装")
        return True

    line(f"  缺少依赖：{', '.join(missing)}")
    line("  正在自动安装：python -m pip install -r requirements.txt")
    rc = _run(cmd + ["-m", "pip", "install", "-r", str(REQ)])
    if rc != 0:
        line("  [!!] 自动安装失败。请手动执行：python -m pip install -r requirements.txt")
        return False
    line("  [OK] 依赖安装完成")
    return True


# ---------------------------------------------------------------- 2. 运行方式

def choose_mode() -> str:
    step("[2/4] 选择运行方式")
    line("  1) 命令行运行（跑完直接生成 PDF / Excel / 网页三端报告）")
    line("  2) 网页运行（启动浏览器界面，可上传文件、在线填 Key）")
    return ask("  请输入 1 或 2（默认 1）：", "1")


# ---------------------------------------------------------------- 3. DeepSeek 配置

def read_key() -> str:
    """按 .env → 环境变量的顺序读取 Key；不打印明文。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            name, sep, value = raw.partition("=")
            if sep and name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


def write_key(key: str) -> None:
    """把 Key 写入 .env（保留其它行）；.env 已在 .gitignore，不入库。"""
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    out, replaced = [], False
    for raw in lines:
        if raw.partition("=")[0].strip() == "DEEPSEEK_API_KEY":
            out.append(f"DEEPSEEK_API_KEY={key}")
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        out.append(f"DEEPSEEK_API_KEY={key}")
    ENV_FILE.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    line(f"  已写入 {ENV_FILE.name}（该文件已加入 .gitignore，不会提交）")


def ensure_key() -> bool:
    """返回是否为在线模式（True=在线、有 Key；False=离线）。"""
    step("[3/4] 检查 DeepSeek 配置")
    key = read_key()
    if key:
        line(f"  [OK] 已检测到 DEEPSEEK_API_KEY（{mask(key)}）")
        line("  1) 在线模式（真实调用 DeepSeek API）")
        line("  2) 离线模式（只读本地缓存，不联网，评审复现推荐）")
        return ask("  请输入 1 或 2（默认 1）：", "1") != "2"

    line("  未检测到 DEEPSEEK_API_KEY。")
    line("  1) 现在输入 Key（在线模式：真实调用 DeepSeek API）")
    line("  2) 跳过（离线模式：只读本地缓存，不联网、不需要 Key）")
    choice = ask("  请输入 1 或 2（默认 2）：", "2")

    if choice == "1":
        key = ask("  请输入 DeepSeek API Key（形如 sk-...）：")
        if key:
            write_key(key)
            line("  [OK] 已配置 Key，使用【在线模式】")
            return True
        line("  [!!] 未输入有效 Key，自动转为【离线模式】")
    else:
        line("  已选择跳过，使用【离线模式】")
    return False


# ---------------------------------------------------------------- 4. 运行

def run_cli(online: bool) -> int:
    folder = clean_path(ask(
        "  请输入材料文件夹路径（直接回车 = 使用仓库自带 demo 数据）："))
    line()

    cmd = PY + ["-m", "fincheck"]
    if folder:
        line(f"  材料文件夹：{folder}")
        cmd += ["ingest", "--from", folder, "--run-id", RUN_ID]
    else:
        line("  未输入路径，使用仓库自带 demo 数据")
        cmd += ["run", "--config", "configs/demo.yaml", "--run-id", RUN_ID]
    if not online:
        cmd.append("--offline")
    line(f"  运行模式：{'在线' if online else '离线'}")
    line()

    rc = _run(cmd)
    if rc != 0:
        line("  [!!] 流程执行失败，请按上面的提示处理后重跑。")
        return rc

    base = ROOT / "runs" / RUN_ID
    line()
    line("  产物目录：runs/%s/" % RUN_ID)
    for name in ("report.pdf", "report.xlsx", "report.html"):
        target = base / name
        if target.exists():
            line(f"    {target.relative_to(ROOT)}")
    line()
    line("  全部完成。")
    return 0


def run_web() -> int:
    line("  正在启动网页界面，浏览器会自动打开 http://localhost:8501")
    line("  关闭网页服务：在本窗口按 Ctrl+C")
    line()
    return _run(PY + ["-m", "streamlit", "run", str(ROOT / "app.py")])


def _setup_console() -> None:
    """把控制台统一成 UTF-8，避免中文输出乱码或直接抛错。

    两种模式都要做：
      - 冻结成 exe 时，外壳进程与它拉起的 python 子进程必须用同一编码写同一个控制台；
      - 源码模式（python launcher.py）下，控制台代码页可能是 437/936/950，
        直接 print 中文会乱码，编码严格时甚至抛 UnicodeEncodeError 中断流程。
    统一控制台代码页、自身三条流与子进程编码（PYTHONIOENCODING）为 UTF-8。
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:                                    # noqa: BLE001
            pass
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> int:
    global PY
    _setup_console()
    line()
    line("  财报一致性量化核查智能体 · 交互式启动器")
    line("  （离线模式不需要网络与 API Key，可直接用于评审复现）")

    found = check_python()
    if found is None:
        line()
        line("  已退出：请安装 Python 3.10+ 后重新运行本程序。")
        return 1
    PY[:] = found

    if not check_deps(PY):
        return 1

    if choose_mode() == "2":
        return run_web()

    online = ensure_key()
    step("[4/4] 运行核查")
    return run_cli(online)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        line("\n  已中断。")
        raise SystemExit(130)
