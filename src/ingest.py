# src/ingest.py
"""文件夹入口：给一个文件夹，自动识别里面的材料并跑完整条流程。

对应比赛"现场可实时处理的样例数据"要求——不预设文件名、不预设目录结构，
把材料往文件夹里一放，程序自己判断哪个是年报、哪个是研究报告。

识别依据是**文件内容**而不是文件名（文件名各人写法不一）：
  - 含"合并资产负债表 / 合并利润表 / 合并现金流量表" → 年报 → 抽结构化字段
  - 含"核心观点 / 投资评级 / 盈利预测 / 证券研究报告" → 研报 → 抽 claim
两者都不像的文件会在报告里列出，不做猜测。
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUPPORTED = {".pdf", ".docx", ".docm"}
ANNUAL_MARKERS = ("合并资产负债表", "合并利润表", "合并现金流量表")
RESEARCH_MARKERS = ("核心观点", "投资评级", "盈利预测", "证券研究报告", "风险提示")
YEAR_RE = re.compile(r"(20\d{2})")


def _docx_text(path: Path) -> str:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return re.sub(r"<[^>]+>", "", xml)


def _pdf_text(path: Path) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".docx", ".docm"):
        return _docx_text(path)
    if suffix == ".pdf":
        return _pdf_text(path)
    raise SystemExit(f"不支持的文件类型：{path.name}（支持 .pdf / .docx）")


def classify(path: Path) -> tuple[str, int]:
    """返回 (类别, 命中标记数)。类别：annual / research / unknown。"""
    text = read_text(path)
    a = sum(1 for m in ANNUAL_MARKERS if m in text)
    r = sum(1 for m in RESEARCH_MARKERS if m in text)
    if a >= 2 and a >= r:
        return "annual", a
    if r >= 2:
        return "research", r
    return "unknown", max(a, r)


def year_from_name(path: Path, fallback: int | None = None) -> int:
    """从文件名里找 4 位年份；找不到就用 fallback。"""
    m = YEAR_RE.search(path.stem)
    if m:
        return int(m.group(1))
    if fallback:
        return fallback
    raise SystemExit(
        f"无法从文件名判断报告期：{path.name}。请在文件名里带上年份"
        f"（如 2024年年度报告.pdf），或用 --year 指定。")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")


def process_folder(folder: Path, out_dir: Path,
                   default_year: int | None = None) -> dict:
    """扫描文件夹 → 分类 → 逐份抽取到 out_dir。

    ★ 结果写入 out_dir（调用方传 run 专属目录），**不写共享的 data/structured/**。
    否则每次 ingest 都会往共享目录里堆文件，而 load_structured 是把目录下所有
    json 全读进来的——堆两次就会把不同批次的记录混在一起用。
    """
    import tools.extract_fields as EF
    import tools.extract_claims as EC

    if not folder.exists():
        raise SystemExit(f"文件夹不存在：{folder}")

    files = sorted(p for p in folder.rglob("*")
                   if p.is_file() and p.suffix.lower() in SUPPORTED)
    if not files:
        raise SystemExit(
            f"文件夹里没有可处理的文件：{folder}\n  支持的类型：.pdf / .docx")

    out_dir.mkdir(parents=True, exist_ok=True)
    log, structured_files, all_claims = [], [], []
    for path in files:
        kind, hits = classify(path)
        if kind == "unknown":
            log.append({"file": path.name, "kind": "unknown", "hits": hits,
                        "action": "跳过：既不像年报也不像研报"})
            continue

        year = year_from_name(path, default_year)
        if kind == "annual":
            records, chunk_index = EF.extract(path, year)
            if not records:
                log.append({"file": path.name, "kind": kind, "hits": hits,
                            "action": "跳过：未抽到任何核心字段"})
                continue
            out = out_dir / f"{path.stem}_{year}.json"
            _write_json(out, records)
            idx = out_dir / "chunk_index.json"
            merged = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else {}
            merged.update(chunk_index)
            _write_json(idx, merged)
            structured_files.append(out)
            log.append({"file": path.name, "kind": kind, "hits": hits, "year": year,
                        "action": f"抽取 {len(records)} 条字段", "out": out.name})
        else:
            tag = f"{year}_" + re.sub(r"[^\w]", "", path.stem)[:12]
            claims = EC.extract(path, tag)
            for c in claims:                      # 加文件前缀，避免多份研报 claim_id 撞车
                c["claim_id"] = f"{tag}::{c['claim_id']}"
            all_claims.extend(claims)
            log.append({"file": path.name, "kind": kind, "hits": hits, "year": year,
                        "action": f"抽取 {len(claims)} 条 claim"})

    claims_path = None
    if all_claims:
        # 放子目录：load_structured 用非递归 glob 读 out_dir/*.json，
        # 分开放可避免研报 claims 被当成财报字段记录
        claims_path = out_dir / "claims" / "report_claims.json"
        _write_json(claims_path, all_claims)

    return {"files": len(files), "log": log,
            "structured_files": structured_files,
            "claims_path": claims_path, "claims_count": len(all_claims)}


def ingest(folder: str, run_id: str, offline: bool = False,
           config_path: str = "configs/demo.yaml", default_year: int | None = None) -> dict:
    """文件夹入口：处理材料 → 直接跑流程。本次解析结果全部落在 runs/<run_id>/ 下。"""
    from src.pipeline import run

    out_dir = ROOT / "runs" / run_id / "ingested"
    result = process_folder(Path(folder), out_dir, default_year)

    print(f"扫描到 {result['files']} 个文件：")
    for e in result["log"]:
        extra = f"（{e.get('year')}）" if e.get("year") else ""
        print(f"  [{e['kind']:8s}] {e['file']}{extra} -> {e['action']}")
    print(f"解析产物 -> runs/{run_id}/ingested/")
    print()

    if not result["structured_files"]:
        print("⚠ 没有识别出年报，财报数据为空——研报核查将没有比对基准。")

    run(config_path, offline=offline, run_id=run_id,
        claims_override=str(result["claims_path"].relative_to(ROOT))
        if result["claims_path"] else None,
        data_dir_override=str(out_dir.relative_to(ROOT))
        if result["structured_files"] else None)
    result["run_id"] = run_id
    return result

