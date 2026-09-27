# src/manifest.py
"""每次运行的清单。只记录元信息，绝不记录密钥与财报正文。"""
import json, time, hashlib, sys, subprocess
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

ROOT = Path(__file__).resolve().parents[1]          # 项目根目录（src 的上一级）

TRACKED_PACKAGES = ("openai", "pydantic", "pandas", "pdfplumber", "python-dotenv")


def _as_rel(p) -> Path:
    """把任意路径规范成相对于项目根目录的形式；项目外的路径则保留绝对路径。
    关键：先 resolve() 转成绝对路径，再 relative_to()，否则相对路径会报 ValueError。"""
    ap = Path(p).resolve()
    try:
        return ap.relative_to(ROOT)
    except ValueError:
        return ap


def sha256_file(p) -> str:
    """文件内容指纹。分块读取，避免大文件占满内存。"""
    ap = Path(p).resolve()
    if not ap.exists():
        raise FileNotFoundError(f"清单要记录的输入文件不存在：{ap}")
    h = hashlib.sha256()
    with ap.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _pkg_versions() -> dict:
    out = {}
    for name in TRACKED_PACKAGES:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def _git_commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=ROOT, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:
        return None


def new_manifest(run_id: str, operator: str, model_cfg: dict,
                 prompts: list[dict], input_files: list) -> dict:
    """在流程开始时创建；结束时用 save_manifest() 补全 calls/outputs 并落盘。
    model_cfg 中不得包含任何密钥。"""
    return {
        "manifest_version": "1.0",
        "run_id": run_id,
        "operator": operator,                       # 只用代号，不写真实姓名
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "finished_at": None,
        "status": "RUNNING",

        # ---- 可复现要素（参与一致性比对）----
        "model": model_cfg,
        "prompts": prompts,                         # [{"prompt_id","version","sha256"}]
        "inputs": [{"file": str(_as_rel(p)), "sha256": sha256_file(p)}
                   for p in input_files],
        "code": {
            "git_commit": _git_commit(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
        "packages": _pkg_versions(),

        # ---- 运行事实（参与结果比对）----
        "calls": {"api_ok": 0, "cache_hit": 0, "failed": 0, "total": 0},
        "outputs": {},
    }


def save_manifest(run_id: str, m: dict) -> Path:
    m["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    p = ROOT / "runs" / run_id / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2, sort_keys=True),
                 encoding="utf-8")
    return p