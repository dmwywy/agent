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
import zipfile
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
    # ★ 2025 年报把这行拆成了"归属于母公司所有者权益" + "（或股东权益）合计"两行
    #   （跨表格片段），金额留在前半行、后半行是空的。只认全名会整条丢掉，
    #   连带 BS02 / LJ05 两条规则判 NA。
    "归属于母公司所有者权益": ("EQUITY_PARENT", "BS", "BAL_END"),
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
    "投资活动产生的现金流量净额": ("CF_INVESTING", "CF", "FY"),
    "筹资活动产生的现金流量净额": ("CF_FINANCING", "CF", "FY"),
    "汇率变动对现金及现金等价物的影响": ("CF_FX_EFFECT", "CF", "FY"),
    "现金及现金等价物净增加额": ("CF_NET_INCREASE", "CF", "FY"),
    "期末现金及现金等价物余额": ("CASH_EQ_END", "CF", "BAL_END"),
}

# ---- 附注明细（C附 维度）----
# 结构："七、合并财务报表项目注释" 下的 "1、 货币资金" 小节，后跟一张
# 【项目 | 期末余额 | 期初余额】表。用于把主表的汇总数拆到明细科目上，
# 从而核对"主表货币资金 = 库存现金 + 银行存款 + 其他货币资金"。
# 每个附注小节的期间性质不同，必须分开标注（interfaces.md §1.5 期间不许混）：
#   货币资金     → 期末余额/期初余额，是**时点数**
#   补充资料     → 本期金额/上期金额，是**时期数**
# 无差别标成 BAL_END 会把时期数当存量，进而与利润表混算。
NOTE_TARGETS = {
    "货币资金": {"period": "BAL", "items": {
        "库存现金": "CASH_ON_HAND", "银行存款": "CASH_IN_BANK",
        "其他货币资金": "CASH_OTHER"}},
    # 用于表间核对：同一"净利润"在利润表与补充资料里应一致；
    # "经营活动现金流量净额"在现金流量表与补充资料里也应一致。
    # 年报里该小节标题写作"金流量表补充资料"（原件漏了"现"字），故用「包含」匹配。
    #
    # 补充资料表是**固定模板**，调节项有十几行，用「包含」匹配而不是相等：
    # 同一行不同年份写法就不同（2024"固定资产折旧、油气资产折耗…"、
    # 2025"固定资产折旧、油气资产折旧…"），相等匹配必漏。
    # ★ 列表**顺序敏感**：先长后短，否则短键会抢走长键的行。
    "现金流量表补充资料": {"period": "FY", "match": "contains", "matchers": [
        ("经营活动产生的现金流量净额", "SUPP_CF_OPERATING"),
        ("现金及现金等价物净增加额", "SUPP_CF_NET_INCREASE"),
        ("净利润", "SUPP_NET_PROFIT"),
        ("资产减值准备", "SUPP_ASSET_IMPAIRMENT"),
        ("信用减值损失", "SUPP_CREDIT_LOSS"),
        ("固定资产折旧", "SUPP_DEPRECIATION"),
        ("使用权资产摊销", "SUPP_AMORT_ROU"),
        ("无形资产摊销", "SUPP_AMORT_INTANGIBLE"),
        ("长期待摊费用摊销", "SUPP_AMORT_LT"),
        ("处置固定资产", "SUPP_DISPOSAL_LOSS"),
        ("固定资产报废损失", "SUPP_SCRAP_LOSS"),
        ("公允价值变动损失", "SUPP_FV_LOSS"),
        ("财务费用", "SUPP_FIN_EXPENSE"),
        ("投资损失", "SUPP_INV_LOSS"),
        ("递延所得税资产减少", "SUPP_DTA_DECREASE"),
        ("递延所得税负债增加", "SUPP_DTL_INCREASE"),
        ("存货的减少", "SUPP_INV_DECREASE"),
        ("经营性应收项目的减少", "SUPP_RECV_DECREASE"),
        ("经营性应付项目的增加", "SUPP_PAYABLE_INCREASE"),
    ]},
}
# 附注小节标题形如 "1、 货币资金√适用"（后面可能粘着适用性标记）。
# ★ 编号分隔符必须同时接受全角顿号与半角句点：实测 2024 年报用 "1、货币资金"，
#   2025 年报用 "8. 货币资金"——同一家公司不同年份写法就不一样，只认一种会漏。
NOTE_HEADING_RE = re.compile(r"^\d+\s*[、.．]\s*([一-龥]{2,12})")
# 附注**子小节**标题形如 "（1）按账龄披露" / "(2).按坏账计提方法分类披露"。
# 必须单独识别：不认它，note 会一直停在上一节的取值上，
# 后面所有表都会被当成"仍在货币资金小节里"错抽。
NOTE_SUBHEADING_RE = re.compile(r"^[（(]\s*\d+\s*[）)]\s*[.．]?\s*([一-龥]{2,14})")

# ---- 按「目标行 + 固定列号」取数的附注表（规则表 7/26 条）----
# (附注小节, 子小节关键字, 目标行, {item_code: 期末列号}, {item_code: 期初列号})
# "按坏账计提方法分类披露"表的合计行形如：
#   合计 | 账面余额 | 比例 | 坏账准备 | 计提比例 | 账面价值 | 期初账面余额 | …
# 列号是硬定位，所以必须靠子小节标题把范围锁死——其他应收款附注的表结构完全相同，
# 不锁小节就会把其他应收款当应收账款。
NOTE_ROW_TARGETS = [
    ("应收账款", "坏账计提方法", "合计",
     {"AR_GROSS": 1, "AR_BADDEBT": 3}, {"AR_GROSS": 6, "AR_BADDEBT": 8}),
]


def _match_row_target(section: str | None, sub: str | None):
    if not section or not sub:
        return None
    for sec, kw, row, cur, prev in NOTE_ROW_TARGETS:
        if section == sec and kw in sub:
            return row, cur, prev
    return None

# ---- 分部数据（第三节 管理层讨论与分析，规则表 11/12 条）----
# 表形如：
#   主营业务分行业情况          ← 小节标题独占一行
#   分行业|营业收入|营业成本|毛利率…  ← 列头
#   酒类|170,611,838,052.02|13,629,995,812.89|…
# 四个小节（行业/产品/地区/销售模式）共处一张表，**必须按小节归属**：
# "酒类"出现在分行业小节，"茅台酒/其他系列酒"出现在分产品小节，
# 不切小节就会把小节标题行或列头行当成数据行。
SEGMENT_SECTIONS = {
    "主营业务分行业情况": "行业",
    "主营业务分产品情况": "产品",
    "主营业务分地区情况": "地区",
    "主营业务分销售模式情况": "销售模式",
}
SEGMENT_HEADER_LABELS = {"分行业", "分产品", "分地区", "销售模式"}
# (小节, 行名) -> (营业收入 item_code, 营业成本 item_code)；成本未取数的填 None
SEGMENT_ITEMS: dict[tuple[str, str], tuple[str | None, str | None]] = {
    ("行业", "酒类"): ("REVENUE_LIQUOR", "COST_LIQUOR"),
    ("产品", "茅台酒"): ("REVENUE_MOUTAI", "COST_MOUTAI"),
    ("产品", "其他系列酒"): ("REVENUE_SERIES", "COST_SERIES"),
    ("产品", "系列酒"): ("REVENUE_SERIES", "COST_SERIES"),
    ("地区", "国内"): ("REVENUE_DOMESTIC", None),
    ("地区", "国外"): ("REVENUE_OVERSEAS", None),
    ("销售模式", "批发代理"): ("REVENUE_WHOLESALE", None),
    ("销售模式", "直销"): ("REVENUE_DIRECT", None),
}
SEGMENT_HEADING_RE = re.compile(r"主营业务分行业.*分产品|主营业务分产品")

# ---- 所有者权益变动表（第十节，规则表 13/15/18 条）----
# 合并表固定 16 列（母公司表 9 列，不取）。列头是**纵向合并单元格**，
# 解析出来的表头行只有 14 个格子、与数据行对不上号（实测 2024/2025 均如此），
# 所以列位置只能按数据行的列序号硬定位，并强制校验列数——宁可整表跳过，
# 也不按错位的列号取数：取错了会变成规则层的一条"异常"，比不取更糟。
#   0 项目 | 1 实收资本 | 2-4 其他权益工具 | 5 资本公积 | 6 减：库存股
#   7 其他综合收益 | 8 专项储备 | 9 盈余公积 | 10 一般风险准备 | 11 未分配利润
#   12 其他 | 13 归母小计 | 14 少数股东权益 | 15 所有者权益合计
EQ_COL_COUNT = 16
EQ_COLUMNS = {"OCI": 7, "SURPLUS": 9, "UNDIST": 11, "PARENT": 13,
              "MINORITY": 14, "TOTAL": 15}
# 行标签（去空白、统一括号与句点后）→ {列名: item_code}
EQ_ROWS_END = {"UNDIST": "SI_UNDIST_END", "SURPLUS": "SI_SURPLUS_END",
               "PARENT": "SI_EQ_PARENT_END", "MINORITY": "SI_EQ_MINORITY_END",
               "TOTAL": "SI_EQ_TOTAL_END"}
EQ_ROWS_BEG = {"UNDIST": "SI_UNDIST_BEG", "SURPLUS": "SI_SURPLUS_BEG",
               "PARENT": "SI_EQ_PARENT_BEG"}
# 综合收益总额行：未分配利润列=归母净利润，其他综合收益列=其他综合收益，小计列=归母综合收益
EQ_ROWS_CI = {"UNDIST": "SI_NP_PARENT", "OCI": "SI_OCI_PARENT",
              "PARENT": "SI_CI_PARENT"}
# 表首行形如 ["项目", "2024 年度"]——年份在第二个格子里，不能用 ^ 锚定
EQ_TITLE_RE = re.compile(r"(\d{4})\s*年\s*度")
# 取数区间：从"合并所有者权益变动表"到"母公司所有者权益变动表"（后者在 CONTEXT_BREAKERS 里）
EQ_HEADING = "合并所有者权益变动表"

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


def extract(src_path: Path, year: int) -> tuple[list[dict], dict]:
    """按扩展名分派：PDF 走文本行扫描，docx 走 Word 表格解析。

    为什么要支持 docx：现场测试时评委给的材料可能是 Word 文件（.doc/.docx），
    而 pdfplumber 只吃 PDF。Word 表格有明确的单元格边界，解析反而比 PDF 更稳。
    """
    suffix = src_path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(src_path, year)
    if suffix in (".docx", ".docm"):
        return _extract_docx(src_path, year)
    if suffix == ".doc":
        raise SystemExit(
            f"不支持旧版 .doc 二进制格式：{src_path.name}\n"
            "  请先用 Word 另存为 .docx（或 PDF）再运行。")
    raise SystemExit(f"无法识别的文件类型：{src_path.name}（支持 .pdf / .docx）")


# ---- docx：按文档顺序遍历「段落 / 表格」，靠标题切换口径状态 ----
_BODY_TOKEN_RE = re.compile(r"<w:tbl>.*?</w:tbl>|<w:p[ >].*?</w:p>", re.S)
_CELL_VALUE_RE = re.compile(r"^-?[\d,]+\.\d{2}$")


def _strip_tags(frag: str) -> str:
    return re.sub(r"<[^>]+>", "", frag).strip()


def _docx_rows(tbl_xml: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<w:tr[ >].*?</w:tr>", tbl_xml, re.S):
        cells = [_strip_tags(tc) for tc in re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)]
        if any(cells):
            rows.append(cells)
    return rows


def _squash(s: str) -> str:
    """去空白 + 统一全角括号/句点。行标签比对必须走这一步：
    同一行 2024 写"（三）利润分配"、2025 写"(三)利润分配"，直接比会漏。"""
    s = re.sub(r"\s+", "", s or "")
    return s.replace("（", "(").replace("）", ")").replace("．", ".")


def _match_note_item(spec: dict, label: str) -> str | None:
    """附注表行标签 → item_code。exact 用于明细科目名，contains 用于固定模板行。"""
    if spec.get("match") == "contains":
        flat = re.sub(r"\s+", "", label)
        # 以冒号结尾的是小节标题，不是数据行。补充资料表里
        # "1．将净利润调节为经营活动现金流量："也含"净利润"，不挡掉就会被当成净利润行。
        if flat.endswith(("：", ":")):
            return None
        for key, code in spec["matchers"]:
            if key in flat:
                return code
        return None
    return spec["items"].get(label.strip())


def _eq_row_targets(label: str) -> dict | None:
    """所有者权益变动表行标签 → {列名: item_code}；不关心的行返回 None。"""
    s = _squash(label)
    if s.endswith("本年期初余额"):
        return EQ_ROWS_BEG
    if s.endswith("本期期末余额"):
        return EQ_ROWS_END
    if s.endswith("综合收益总额"):
        return EQ_ROWS_CI
    if s.endswith("提取盈余公积"):
        return {"UNDIST": "SI_SURPLUS_APPROP"}
    if "对所有者" in s and s.endswith("的分配"):
        return {"UNDIST": "SI_DIVIDEND"}
    return None


def _emit(records, chunk_index, seen, *, code, raw, value, period, ptype,
          statement, locator, chunk_id, raw_text, src_name, src_hash, note_ref=None):
    """统一构造字段记录（interfaces.md §2.1）。已出现过的 (code, period, ptype) 跳过。"""
    if value is None or (code, period, ptype) in seen:
        return
    seen.add((code, period, ptype))
    rec = {
        "item_code": code, "item_raw": raw, "value": value.replace(",", ""),
        "unit_raw": "元", "period_type": ptype, "period": period,
        "scope": "CONSOLIDATED", "statement": statement,
        "locator": locator, "chunk_id": chunk_id, "raw_text": raw_text,
        "source_hash": src_hash, "parse_status": "OK", "confidence": 1.0,
    }
    if note_ref:
        rec["note_ref"] = note_ref
    chunk_index.setdefault(chunk_id, {"locator": locator, "source": src_name,
                                      "chars": len(raw_text)})
    records.append(rec)


def _scan_segment_table(rows):
    """分部表 → [(code, value, raw_text, row_no, label)]。

    只认「小节标题行 → 列头行 → 数据行」的结构；列头行用于确认
    第 1 列是营业收入、第 2 列是营业成本，位置不符就整表不取。
    label 取原文行名（"茅台酒"/"国内"…），它同时是 item_raw ——
    必须真的出现在 raw_text 里，否则证据包含性校验过不了（实测踩过：
    用"产品"这种小节名当 item_raw，10 条记录全被打成"待人工复核"）。
    """
    out, section, cols_ok = [], None, False
    for ri, cells in enumerate(rows):
        if len(cells) == 1:
            section = SEGMENT_SECTIONS.get(_squash(cells[0]))
            cols_ok = False
            continue
        if section is None:
            continue
        if cells[0].strip() in SEGMENT_HEADER_LABELS:
            cols_ok = (len(cells) > 2 and "营业收入" in cells[1] and "营业成本" in cells[2])
            continue
        if not cols_ok:
            continue
        label = cells[0].strip()
        pairs = SEGMENT_ITEMS.get((section, _squash(label)))
        if not pairs:
            continue
        for code, idx in zip(pairs, (1, 2)):
            if code and idx < len(cells) and _CELL_VALUE_RE.match(cells[idx].strip()):
                out.append((code, cells[idx].strip(), " | ".join(cells), ri + 1, label))
    return out


def _scan_equity_table(rows, eq_year):
    """合并所有者权益变动表 → ([(code, value, period, ptype, raw_text, row_no)], 年度)。

    ★ 一份权益变动表在 Word 里可能被拆成**多个表格对象**（实测 2024/2025 都是
      "表主体 + 续表"），续表没有表头年份。因此年度要由调用方跨表传递：
      见到新表头就换年度，见不到就沿用上一张，否则"四、本期期末余额"（在续表里）
      会被整表丢掉——那正是本表最关键的期末数。
    """
    if not rows:
        return [], eq_year
    m = EQ_TITLE_RE.search(_squash(" ".join(rows[0])))
    if m:
        eq_year = int(m.group(1))
    if eq_year is None:
        return [], None        # 不在权益变动表区间，或首表就没有年份：不猜
    out = []
    for ri, cells in enumerate(rows):
        if len(cells) != EQ_COL_COUNT:
            continue            # 列数不符：宁可不取，也不按错位的列号取数
        targets = _eq_row_targets(cells[0])
        if not targets:
            continue
        # 期间性质由**行**决定：期末/期初是时点，综合收益与利润分配是时期（§1.5）
        if targets is EQ_ROWS_BEG:
            period, ptype = f"{eq_year - 1}-12-31", "BAL_BEG"
        elif targets is EQ_ROWS_END:
            period, ptype = f"{eq_year}-12-31", "BAL_END"
        else:
            period, ptype = str(eq_year), "FY"
        raw_text = " | ".join(cells)
        for col, code in targets.items():
            v = cells[EQ_COLUMNS[col]].strip()
            if _CELL_VALUE_RE.match(v):
                out.append((code, v, period, ptype, raw_text, ri + 1, cells[0].strip()))
    return out, eq_year


def _extract_docx(src_path: Path, year: int) -> tuple[list[dict], dict]:
    """从 Word 文档抽数。与 PDF 版共用同一套口径状态机与科目别名表。

    定位方式用 locator（章节 + 表序号 + 行号）代替 page：
    docx 是流式文档，没有固定分页，硬编页码等于伪造证据。
    """
    src_hash = sha256_file(src_path)
    xml = zipfile.ZipFile(src_path).read("word/document.xml").decode("utf-8")
    body = xml[xml.find("<w:body>"):]

    records: list[dict] = []
    chunk_index: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    state: str | None = None
    note: str | None = None          # 当前生效的附注目标小节（见 NOTE_TARGETS）
    section: str | None = None       # 最近的一级附注小节名（含非目标小节）
    sub: str | None = None           # 最近的附注子小节名（如"按账龄披露"）
    heading = ""
    table_no = 0
    seg_pending = False              # 下一张表是否为主营业务分部表
    eq_state = False                 # 当前是否处于合并所有者权益变动表区间
    eq_year: int | None = None       # 当前权益变动表的年度（续表沿用上一张）

    for m in _BODY_TOKEN_RE.finditer(body):
        frag = m.group(0)
        if frag.startswith("<w:tbl>"):
            table_no += 1
            rows = _docx_rows(frag)

            # 分部表（第三节）：紧跟在"主营业务分行业…分产品…情况"标题后的那张表
            if seg_pending:
                seg_pending = False
                for code, value, raw_text, row_no, label in _scan_segment_table(rows):
                    _emit(records, chunk_index, seen, code=code, raw=label,
                          value=value, period=str(year), ptype="FY",
                          statement="MDNA",
                          locator=f"第三节 主营业务分部表 / 表#{table_no} / 第{row_no}行",
                          chunk_id=f"s{table_no:03d}_r{row_no:02d}", raw_text=raw_text,
                          src_name=src_path.name, src_hash=src_hash)
                continue

            # 合并所有者权益变动表（第十节）
            if eq_state:
                eq_rows, eq_year = _scan_equity_table(rows, eq_year)
                for code, value, period, ptype, raw_text, row_no, label in eq_rows:
                    _emit(records, chunk_index, seen, code=code, raw=label,
                          value=value, period=period, ptype=ptype, statement="EQ",
                          locator=f"{EQ_HEADING} {eq_year}年度 / 表#{table_no} / 第{row_no}行",
                          chunk_id=f"q{table_no:03d}_r{row_no:02d}", raw_text=raw_text,
                          src_name=src_path.name, src_hash=src_hash)
                if eq_year:
                    continue        # 已确认为权益变动表：不再当主表/附注表扫

            # 附注「目标行 + 固定列号」表（应收账款坏账计提）：按 (小节, 子小节) 锁定
            rt = _match_row_target(section, sub)
            if rt:
                target_row, cur_cols, prev_cols = rt
                for ri, cells in enumerate(rows):
                    if _squash(cells[0]) != target_row:
                        continue
                    locator = f"{section}（附注）/ {sub} / 表#{table_no} / 第{ri + 1}行"
                    chunk_id = f"n{table_no:03d}_r{ri + 1:02d}"
                    for cols, period in ((cur_cols, year), (prev_cols, year - 1)):
                        for code, ci in cols.items():
                            if ci >= len(cells) or not _CELL_VALUE_RE.match(cells[ci].strip()):
                                continue
                            _emit(records, chunk_index, seen, code=code, raw=cells[0].strip(),
                                  value=cells[ci].strip(), period=f"{period}-12-31",
                                  ptype="BAL_END" if period == year else "BAL_BEG",
                                  statement="NOTE", locator=locator, chunk_id=chunk_id,
                                  raw_text=" | ".join(cells), src_name=src_path.name,
                                  src_hash=src_hash)
                    break          # 只取本子小节的第一张表的合计行

            # 附注明细表：按"项目 | 期末 | 期初"取目标明细科目
            if note in NOTE_TARGETS:
                spec = NOTE_TARGETS[note]
                for ri, cells in enumerate(rows):
                    code = _match_note_item(spec, cells[0])
                    if not code:
                        continue
                    vals = [c for c in cells[1:] if _CELL_VALUE_RE.match(c.strip())]
                    if not vals:
                        continue
                    locator = f"{note}（附注） / 表#{table_no} / 第{ri + 1}行"
                    chunk_id = f"n{table_no:03d}_r{ri + 1:02d}"
                    chunk_index.setdefault(chunk_id, {
                        "locator": locator, "source": src_path.name,
                        "chars": len(" ".join(cells))})
                    for value, period in zip(vals, (year, year - 1)):
                        if (code, str(period)) in seen:
                            continue
                        seen.add((code, str(period)))
                        if spec["period"] == "BAL":
                            ptype = "BAL_END" if period == year else "BAL_BEG"
                            pval = f"{period}-12-31"
                        else:
                            ptype = "FY"          # 时期数：本期/上期金额
                            pval = str(period)
                        records.append({
                            "item_code": code, "item_raw": cells[0].strip(),
                            "value": value.replace(",", ""), "unit_raw": "元",
                            "period_type": ptype, "period": pval,
                            "scope": "CONSOLIDATED",
                            "statement": "NOTE", "locator": locator,
                            "chunk_id": chunk_id, "raw_text": " | ".join(cells),
                            "source_hash": src_hash, "parse_status": "OK",
                            "confidence": 1.0,
                        })
            if state is None:
                continue
            for ri, cells in enumerate(rows):
                hit = LABEL_ALIASES.get(normalize_label(cells[0]))
                if not hit:
                    continue
                item_code, stmt, period_type = hit
                if stmt != state:
                    continue
                values = [c for c in cells[1:] if _CELL_VALUE_RE.match(c.strip())]
                if not values:
                    continue
                locator = f"{heading or '未标章节'} / 表#{table_no} / 第{ri + 1}行"
                chunk_id = f"t{table_no:03d}_r{ri + 1:02d}"
                chunk_index.setdefault(chunk_id, {
                    "locator": locator, "source": src_path.name,
                    "chars": len(" ".join(cells))})
                for value, period in zip(values, (year, year - 1)):
                    if (item_code, str(period)) in seen:
                        continue
                    ptype = period_type
                    if period_type.startswith("BAL") and period == year - 1:
                        ptype = "BAL_BEG"
                    seen.add((item_code, str(period)))
                    records.append({
                        "item_code": item_code,
                        "item_raw": cells[0],
                        "value": value.replace(",", ""),
                        "unit_raw": "元",
                        "period_type": ptype,
                        "period": f"{period}-12-31" if period_type.startswith("BAL") else str(period),
                        "scope": "CONSOLIDATED",
                        "statement": stmt,
                        "locator": locator,          # docx 源的定位凭据（§2.1 v1.3）
                        "chunk_id": chunk_id,
                        "raw_text": " | ".join(cells),
                        "source_hash": src_hash,
                        "parse_status": "OK",
                        "confidence": 1.0,
                    })
        else:
            text = _strip_tags(frag)
            if SEGMENT_HEADING_RE.search(text):
                seg_pending = True    # 下一张表即主营业务分部表
            if text in CONSOLIDATED_TITLES:
                state = CONSOLIDATED_TITLES[text]
                note = section = sub = None
                heading = text        # 进入主表：章节名用表标题
            elif text in CONTEXT_BREAKERS:
                state = None          # 遇母公司报表/其他主表即切断，不混口径
                note = section = sub = None
                heading = text
                # 权益变动表的取数区间：只在"合并"标题到"母公司"标题之间（不混口径）
                eq_state = (text == EQ_HEADING)
                eq_year = None        # 进入区间即清空年度，避免沿用上一份报表的年度
            else:
                nm = NOTE_HEADING_RE.match(text)
                sm = NOTE_SUBHEADING_RE.match(text)
                # 先按「包含」匹配已知附注小节（原件标题可能有错字，如"金流量表补充资料"），
                # 匹配不到再退回编号标题本身（那种小节不在 NOTE_TARGETS 里，不会被抽）
                hit_note = next((k for k in NOTE_TARGETS if k in text), None)
                if hit_note:
                    section, note, sub = hit_note, hit_note, None
                elif nm:
                    section, sub = nm.group(1), None
                    note = section if section in NOTE_TARGETS else None
                elif sm:
                    # 子小节：一级小节不变，但"直接跟在节标题下的目标表"区间到此为止
                    sub, note = sm.group(1), None
                if text and state is None:
                    # 只在表**外**更新标题。表内若也更新，locator 会被
                    # "单位：元 币种：人民币"这类紧跟标题的行覆盖掉。
                    heading = text
    return records, chunk_index


def _extract_pdf(pdf_path: Path, year: int) -> tuple[list[dict], dict]:
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
    ap = argparse.ArgumentParser(description="从年报 PDF / Word 抽取结构化字段")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="年报文件（.pdf 或 .docx），按扩展名自动识别")
    src.add_argument("--pdf", help="等同 --input，保留旧参数名")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", required=True, help="输出文件名前缀，如 贵州茅台_2024")
    args = ap.parse_args()

    path = Path(args.input or args.pdf)
    records, chunk_index = extract(path, args.year)

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

    where = "page" if records and "page" in records[0] else "locator"
    print(f"提取 {len(records)} 条字段记录（来源 {path.suffix}，定位方式 {where}）"
          f" -> data/structured/{args.out}.json")
    print(f"chunk_index 共 {len(merged)} 条 -> data/structured/chunk_index.json")
    missing = {v[0] for v in LABEL_ALIASES.values()} - {r["item_code"] for r in records}
    if missing:
        print("未提取到（需人工核对原文措辞）：", sorted(missing))


if __name__ == "__main__":
    main()
