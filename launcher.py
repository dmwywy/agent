# -*- coding: utf-8 -*-
"""交互式启动器：环境自检 → 选择命令行 / 网页 → 检查 DeepSeek 配置 → 输入材料文件夹 → 运行。

入口（二选一）：
    双击仓库根目录的 `一键复现.bat`
    或在项目根目录执行：python launcher.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQ = ROOT / "requirements.txt"
ENV_FILE = ROOT / ".env"
RUN_ID = "reproduce"

# 依赖导入名（requirements.txt 里对应的包：openai / pydantic / PyYAML /
# pdfplumber / reportlab / openpyxl / pandas / streamlit）
NEEDED = ["openai", "pydantic", "yaml", "pdfplumber",
          "reportlab", "openpyxl", "pandas", "streamlit"]


def line(text: str = "") -> None:
    print(text)


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


# ---------------------------------------------------------------- 1. 环境自检

def check_deps() -> bool:
    step("[1/4] 环境自检")
    try:
        print(f"  Python  {sys.version.split()[0]}  ({sys.executable})")
    except Exception:                                        # noqa: BLE001
        pass

    missing = []
    for mod in NEEDED:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)

    if not missing:
        line("  [OK] 依赖齐备，无需安装")
        return True

    line(f"  缺少依赖：{', '.join(missing)}")
    line("  正在自动安装：python -m pip install -r requirements.txt")
    rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(REQ)],
                         cwd=ROOT)
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

    cmd = [sys.executable, "-m", "fincheck"]
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

    rc = subprocess.call(cmd, cwd=ROOT)
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
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py")],
                           cwd=ROOT)


def main() -> int:
    line()
    line("  财报一致性量化核查智能体 · 交互式启动器")
    line("  （离线模式不需要网络与 API Key，可直接用于评审复现）")

    if not check_deps():
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
