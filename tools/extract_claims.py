# tools/extract_claims.py
"""研报 docx → 句子级 claims（供 match_claim 任务使用）。

职责边界：
  本程序只做"把研报切成可核查的句子"这件确定性的事——切句、定位、抽出句子里的数字。
  **不判断这句话对不对**，也不把它对应到哪个财务科目（那由模型做，见 src/pipeline.run_match_claim）。
  数值比较由规则侧完成。

用法：
    python tools/extract_claims.py --docx <研报.docx> --tag 2024 --out 研报claims_2024
"""
import argparse
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 章节标题（作为 soft 分节，便于报告里定位）
SECTION_RE = re.compile(r"^(核心观点|盈利预测与投资建议|投资建议|风险提示|盈利预测)$")

# 财务数字：金额、百分比、倍数、吨
FIG_RE = re.compile(
    r"-?\d[\d,]*(?:\.\d+)?\s*(?:亿元|万元|百万元|万吨|元|%|pcts|pct|倍|吨)")
# 断句：中文句末标点；分号也断开（研报里分号常并列多个独立数据）
SENT_SPLIT_RE = re.compile(r"(?<=[。！？；])")
# 明显不是"财报数据引用"的句子：日期、联系人、股价行情等
NOISE_RE = re.compile(r"证券研究报告|分析师|联系人|收盘价|最高价|成交额|@|\.com|资料来源|评级|买入|增持|优于大市|中性")


def read_docx(path: Path) -> tuple[list[str], list[list[list[str]]]]:
    """返回 (段落文本列表, 表格列表)。表格 = [表][行][单元格]。"""
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    # 去掉表格，剩下的按段落切
    body_wo_tables = re.sub(r"<w:tbl>.*?</w:tbl>", "\n", xml, flags=re.S)
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", body_wo_tables, re.S):
        text = re.sub(r"<[^>]+>", "", p).strip()
        if text:
            paras.append(text)

    tables = []
    for tb in re.findall(r"<w:tbl>.*?</w:tbl>", xml, re.S):
        rows = []
        for tr in re.findall(r"<w:tr[ >].*?</w:tr>", tb, re.S):
            cells = [re.sub(r"<[^>]+>", "", tc).strip()
                     for tc in re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return paras, tables


def extract(path: Path, tag: str, tables_as_claims: bool = True) -> list[dict]:
    paras, tables = read_docx(path)
    claims: list[dict] = []
    section = ""
    n = 0

    for pi, para in enumerate(paras):
        if SECTION_RE.match(para):
            section = para
            continue
        if NOISE_RE.search(para):
            continue
        for sent in SENT_SPLIT_RE.split(para):
            sent = sent.strip()
            if len(sent) < 8:
                continue
            figs = FIG_RE.findall(sent)
            if not figs:
                continue
            n += 1
            claims.append({
                "claim_id": f"r{tag}_p{pi:02d}_{n:03d}",
                "source": path.name,
                "section": section,
                "para": pi,
                "kind": "TEXT",
                "sentence": sent,
                "figures": figs,
            })

    if tables_as_claims:
        for ti, rows in enumerate(tables):
            for ri, cells in enumerate(rows):
                line = " | ".join(cells)
                if len(FIG_RE.findall(line)) < 2:
                    continue        # 至少两个数字才值得核查，滤掉表头与说明行
                n += 1
                claims.append({
                    "claim_id": f"r{tag}_t{ti:02d}_{ri:02d}",
                    "source": path.name,
                    "section": f"表{ti + 1}",
                    "para": None,
                    "kind": "TABLE_ROW",
                    "sentence": line,
                    "figures": FIG_RE.findall(line),
                })
    return claims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", required=True)
    ap.add_argument("--tag", required=True, help="期间标签，如 2024")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    path = Path(args.docx)
    claims = extract(path, args.tag)

    out_dir = ROOT / "data" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.out}.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    text_n = sum(1 for c in claims if c["kind"] == "TEXT")
    print(f"抽取 {len(claims)} 条 claim（正文 {text_n} / 表格 {len(claims) - text_n}）"
          f" -> data/demo/{args.out}.json")


if __name__ == "__main__":
    main()
