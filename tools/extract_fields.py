# tools/extract_fields.py
"""财报 PDF → 结构化字段记录（数字经济A 的交付物，按 docs/interfaces.md §2.1 输出）。

设计要点：
  1. 表格的行列归属与数值由本程序确定性抽取，**不调用任何模型**（interfaces.md §5.3 禁则2）。
  2. 每条记录都带证据三要素 page / chunk_id / raw_text（§2.1 字段 [9][10][11]）。
  3. raw_text 原样保留该行文本，使"科目名真实出现在原文中"这一包含性校验可执行。
  4. 找不到的字段不写入（§2.1(3)：不用 null 或 0 表示未披露）。
  5. 只在"合并"口径的表内取数，遇到母公司报表标题立即切断（§1.5 口径不许混）。

处理 PDF 的两个现实问题：
  - 长科目名被折行：  "所有者权益（或股东权" / "242,011,315,120.60 ..." / "益）合计"
  - 科目名带层级前缀："一、营业总收入"、"其中：营业收入"

用法：
    python tools/extract_fields.py --pdf <年报.pdf> --year 2024 --out 贵州茅台_2024
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]

# ---- 主表识别：状态机逐页扫描 ----
# 表标题只出现在主表"起始页"，续页没有标题，因此不能用"找锚点页"定范围；
# 必须在扫描过程中维持"当前处于哪张表"的状态。资产负债表 58-60 页，第 61 页起是
# 母公司报表——若不用状态机切断，母公司数据会混进合并口径（违反 interfaces.md §1.5）。
CONSOLIDATED_TITLES = {
    "合并资产负债表": "BS",
    "合并利润表": "IS",
    "合并现金流量表": "CF",
}
CONTEXT_BREAKERS = {
    "母公司资产负债表", "母公司利润表", "母公司现金流量表",
    "合并所有者权益变动表", "母公司所有者权益变动表",
}

# ---- 目标科目：实际文本（归一化后）-> (item_code, 所属报表, 期间类型) ----
LABEL_ALIASES = {
    "资产总计": ("TOTAL_ASSETS", "BS", "BAL_END"),
    "负债合计": ("TOTAL_LIAB", "BS", "BAL_END"),
    "所有者权益合计": ("TOTAL_EQUITY", "BS", "BAL_END"),
    "归属于母公司所有者权益合计": ("EQUITY_PARENT", "BS", "BAL_END"),
    "少数股东权益": ("EQUITY_MINORITY", "BS", "BAL_END"),
    "流动资产合计": ("CURRENT_ASSETS", "BS", "BAL_END"),
    "流动负债合计": ("CURRENT_LIAB", "BS", "BAL_END"),
    "货币资金": ("CASH_END", "BS", "BAL_END"),
    "应收账款": ("AR_NET", "BS", "BAL_END"),
    "存货": ("INVENTORY", "BS", "BAL_END"),
    "固定资产": ("FIXED_ASSETS", "BS", "BAL_END"),
    "营业总收入": ("TOTAL_REVENUE", "IS", "FY"),
    "营业收入": ("REVENUE", "IS", "FY"),
    "营业成本": ("OPERATING_COST", "IS", "FY"),
    "营业利润": ("OPERATING_PROFIT", "IS", "FY"),
    "利润总额": ("TOTAL_PROFIT", "IS", "FY"),
    "所得税费用": ("INCOME_TAX", "IS", "FY"),
    "净利润": ("NET_PROFIT", "IS", "FY"),
    "归属于母公司股东的净利润": ("NP_PARENT", "IS", "FY"),
    "归属于母公司所有者的净利润": ("NP_PARENT", "IS", "FY"),
    "经营活动产生的现金流量净额": ("CF_OPERATING", "CF", "FY"),
    "现金及现金等价物净增加额": ("CF_NET_INCREASE", "CF", "FY"),
    "期末现金及现金等价物余额": ("CASH_EQ_END", "CF", "BAL_END"),
}

# 科目名正则：非数字开头，可含括号冒号汉字，后接可选附注号与两个金额
ROW_RE = re.compile(
    r"^(?P<label>[^\d\s][^\d]*?)\s+(?:(?P<note>\d{1,2})\s+)?"
    r"(?P<cur>-?[\d,]+\.\d{2})(?:\s+(?P<prev>-?[\d,]+\.\d{2}))?\s*$"
)
# 纯数字行（科目名被完全折到上一行时出现）
NUM_ONLY_RE = re.compile(
    r"^(?P<cur>-?[\d,]+\.\d{2})(?:\s+(?P<prev>-?[\d,]+\.\d{2}))?$"
)
# 层级前缀：一、 其中： 减： 加： 1. （一） 等
PREFIX_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+、|其中：|减：|加：|\d+\.|（[一二三四五六七八九十]+）)+")
# 报表附注式后缀： "（亏损以"－"号填列）"、"（净亏损以…"、"（或股东权益）"，
# 以及被换行截断、没有右括号的残段
PAREN_TRIM_RE = re.compile(r"（[^）]*(?:填列|亏损|损失|或股东权益)[^）]*）|（[^）]*$")
NOISE_RE = re.compile(r"\s+")


def normalize_label(s: str) -> str:
    """归一化科目名：去层级前缀、去附注式后缀，得到可与标准科目比对的形式。

    处理的真实写法：
      一、营业总收入 / 其中：营业收入 / 三、营业利润（亏损以"－"号填列）
      所有者权益（或股东权益）合计 / 归属于母公司股东的净利润（净…（折行截断）
    """
    s = PREFIX_RE.sub("", s.strip())
    return NOISE_RE.sub("", PAREN_TRIM_RE.sub("", s))


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_fragment(s: str) -> bool:
    """判断一行是否是"被折行的科目名残段"。
    区别于"流动资产："这类小标题：残段不含数字、不以冒号结尾。
    要先剥掉"1."这类层级编号，否则编号里的数字会被误判成"含数字"。"""
    core = PREFIX_RE.sub("", s.strip())
    return bool(core) and not re.search(r"\d", core) and not core.endswith("：")


def scan_rows(numbered: list[tuple[int, str]]) -> list[tuple[str, str | None, str | None, int]]:
    """把被 PDF 换行切断的科目名重新拼起来，返回 (科目名, 本期值, 上期值, 页码)。

    例：[(61,"归属于母公司所有者权益"), (61,"233,105,984,399.47 215,668,571,607.43"),
         (61,"（或股东权益）合计")]
        -> ("归属于母公司所有者权益（或股东权益）合计", "233...", "215...", 61)

    只回看/前看**紧邻一行**：若允许任意累积，小标题（"流动资产："）会被误拼进下一个科目名。
    """
    texts = [t for _, t in numbered]
    rows, i = [], 0
    while i < len(texts):
        line = texts[i]
        m = ROW_RE.match(line)
        if m:
            rows.append((m.group("label").strip(), m.group("cur"),
                         m.group("prev"), numbered[i][0]))
        else:
            mn = NUM_ONLY_RE.match(line)
            # 纯数字行：科目名被折到上一行（可能还有下一行残余）
            if mn and i > 0 and _is_fragment(texts[i - 1].strip()):
                label = texts[i - 1].strip()
                if i + 1 < len(texts) and _is_fragment(texts[i + 1].strip()):
                    label += texts[i + 1].strip()
                    i += 1
                rows.append((label, mn.group("cur"), mn.group("prev"), numbered[i][0]))
        i += 1
    return rows


def extract(pdf_path: Path, year: int) -> tuple[list[dict], dict]:
    """状态机逐页扫描：只在"合并"口径的表内抽数，遇到母公司报表即切断。"""
    src_hash = sha256_file(pdf_path)
    records: list[dict] = []
    chunk_index: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()      # (item_code, period) 去重

    # 逐页扫描，按"当前处于哪张合并主表"切分成段落。
    # 段落跨页保留（表标题只在起始页出现），遇到母公司报表标题即结束当前段落。
    segments: list[dict] = []
    current_stmt: str | None = None
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for ln in (page.extract_text() or "").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                if ln in CONSOLIDATED_TITLES:
                    current_stmt = CONSOLIDATED_TITLES[ln]
                    segments.append({"stmt": current_stmt, "lines": []})
                elif ln in CONTEXT_BREAKERS:
                    # 只停止归集，**不清空已收集的行**：
                    # 资产负债表尾部数据正好排在"母公司资产负债表"标题之前
                    current_stmt = None
                elif current_stmt is not None:
                    segments[-1]["lines"].append((page_no, ln))

    for seg in segments:
        for label, cur, prev, page_no in scan_rows(seg["lines"]):
            hit = LABEL_ALIASES.get(normalize_label(label))
            if not hit:
                continue
            item_code, stmt, period_type = hit
            if stmt != seg["stmt"]:
                continue

            chunk_id = f"p{page_no:03d}_t1"
            chunk_index.setdefault(chunk_id, {
                "page": page_no, "source": pdf_path.name, "chars": len(label)})

            for value, period in ((cur, year), (prev, year - 1)):
                if value is None or (item_code, str(period)) in seen:
                    continue
                ptype = period_type
                # 时点数：本期为期末余额、上期为期初余额（§1.5 期间不许混）
                if period_type.startswith("BAL") and period == year - 1:
                    ptype = "BAL_BEG"
                seen.add((item_code, str(period)))
                records.append({
                    "item_code": item_code,
                    "item_raw": label,
                    "value": value.replace(",", ""),
                    "unit_raw": "元",
                    "period_type": ptype,
                    "period": f"{period}-12-31" if period_type.startswith("BAL") else str(period),
                    "scope": "CONSOLIDATED",
                    "statement": stmt,
                    "page": page_no,
                    "chunk_id": chunk_id,
                    "raw_text": label,
                    "source_hash": src_hash,
                    "parse_status": "OK",
                    "confidence": 1.0,
                })
    return records, chunk_index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", required=True, help="输出文件名前缀，如 贵州茅台_2024")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    records, chunk_index = extract(pdf_path, args.year)

    out_dir = ROOT / "data" / "structured"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.out}.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    # chunk_index 是全项目公共映射表，多份报告合并写入
    idx_path = out_dir / "chunk_index.json"
    merged = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
    merged.update(chunk_index)
    idx_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")

    print(f"提取 {len(records)} 条字段记录 -> data/structured/{args.out}.json")
    print(f"chunk_index 共 {len(merged)} 条 -> data/structured/chunk_index.json")
    missing = {v[0] for v in LABEL_ALIASES.values()} - {r["item_code"] for r in records}
    if missing:
        print("未提取到（需人工核对原文措辞）：", sorted(missing))


if __name__ == "__main__":
    main()
