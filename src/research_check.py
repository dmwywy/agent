# src/research_check.py
"""C研 维度：研报引用核查的**确定性计算**部分。

职责划分（对应 interfaces.md 的技术路线）：
  - 模型（match_claim）只回答"这句话在说哪个科目、哪个期间"；
  - 本模块负责"这句话里的数值和财报对不对得上"——数值一律由程序产生与比较。

★ 为什么不让模型直接给出句子里的金额：
  interfaces.md §5.3 禁则(1)「模型不产出数值」。金额取自研报原文（程序自己持有），
  关联关系由"指标名 → 其后最近的金额"这一确定性规则建立，可复核、可复现。

产出格式遵循 interfaces.md §4.1（rule results），供 CSI 评分与报告侧消费。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

RULE_ID = "RC01@v1.0"          # 研报引用一致性：一条规则，多个实例（每条 claim 一例）
YOY_RULE_ID = "RC02@v1.0"      # 研报引用同比核验：同上，只在不符时产出结果
TOLERANCE_PROFILE = "REPORT_REF"

# 金额+单位 → 换算到"元"的乘数。键为**合法**单位写法。
UNIT_SCALE = {
    "万亿": Decimal("1000000000000"),
    "亿元": Decimal("100000000"),
    "亿":   Decimal("100000000"),
    "百万元": Decimal("1000000"),
    "万元": Decimal("10000"),
    "万":   Decimal("10000"),
    "千元": Decimal("1000"),
    "千":   Decimal("1000"),
    "元":   Decimal("1"),
}
# 单位整体捕获（可含多个量级字，如"亿万元"），再校验是否合法。
# 排除"万吨/万股"等非金额单位：负向先行断言挡掉，避免把销量当营收。
NUM_RE = re.compile(
    r"(-?)\s*(\d[\d,]*(?:\.\d+)?)\s*([万亿千百]*元|[万亿])(?![吨股人件])")

# 研报口语化指标名 → 标准科目编号（fields.yaml 的 zh 为正式名，此处补研报简写）。
# ★ 刻意不收"营收""总收入"这类过于泛化的词：研报里
#     "茅台酒 2024 年营收 1495.3 亿元"
#   中的"营收"是普通名词而非指标名，收进来会把**分部营收**误挂到**总营收**上，
#   造成用 1495.3 亿去比 1741.4 亿的误报。宁可少匹配，也不误报。
ALIASES = {
    "营业总收入": "TOTAL_REVENUE",
    "营业收入": "REVENUE",
    "归母净利润": "NP_PARENT", "归母净利": "NP_PARENT",
    "归属于母公司股东的净利润": "NP_PARENT", "归属于母公司所有者的净利润": "NP_PARENT",
    "净利润": "NET_PROFIT",
    "营业成本": "OPERATING_COST",
    "营业利润": "OPERATING_PROFIT",
    "利润总额": "TOTAL_PROFIT",
    "所得税费用": "INCOME_TAX",
    "资产总计": "TOTAL_ASSETS", "总资产": "TOTAL_ASSETS",
    "负债合计": "TOTAL_LIAB",
    "所有者权益合计": "TOTAL_EQUITY",
    "货币资金": "CASH_END",
    "应收账款": "AR_NET",
    "存货": "INVENTORY",
    "固定资产": "FIXED_ASSETS",
    "茅台酒": "REVENUE_MOUTAI",
    "系列酒": "REVENUE_SERIES",
    "经营活动产生的现金流量净额": "CF_OPERATING",
    # v1.2：研报高频指标——补充口语化写法（正式名已由 fields.yaml 的 zh 覆盖）
    "毛利率": "RATIO_GROSS", "净利率": "RATIO_NET", "资产负债率": "RATIO_LEV",
    "销售费用": "SELLING_EXPENSE", "管理费用": "ADMIN_EXPENSE",
    "研发费用": "RD_EXPENSE", "财务费用": "FIN_EXPENSE", "税金及附加": "TAX_SURCHARGE",
    "合同负债": "CONTRACT_LIAB", "应付账款": "AP",
    "短期借款": "SHORT_TERM_LOAN", "长期借款": "LONG_TERM_LOAN",
}

# 比率类指标：真值由程序计算（src.derived.ratio_records），句中数值以百分号为单位，
# 用专门的百分比解析器归属——与金额解析器分开，避免"增长8%"被误挂到金额科目上。
RATIO_CODES = {"RATIO_GROSS", "RATIO_NET", "RATIO_LEV"}
PCT_RE = re.compile(r"(-?)\s*(\d[\d,]*(?:\.\d+)?)\s*%")

# 净利率口径互认：研报口语里的"净利率"经常写的是**归母净利率**（茅台 2025 即如此：
# 研报 47.8% 实为归母口径，而程序真值 NET_PROFIT/TOTAL_REVENUE = 49.6%）。
# 两个口径都由程序从同一批结构化数据算出。主口径判不一致时，用另一口径复核一次：
# 落在容差内即视为**列报口径不同**而非引用错误，避免拿两个口径互相扣分。
MARGIN_ALT = {"RATIO_NET": "RATIO_NPP", "RATIO_NPP": "RATIO_NET"}
MARGIN_PARTS = {"RATIO_NET": ("NET_PROFIT", "TOTAL_REVENUE"),
                "RATIO_NPP": ("NP_PARENT", "TOTAL_REVENUE")}
MARGIN_ZH = {"RATIO_NET": "净利率（净利润÷营业总收入）",
             "RATIO_NPP": "归母净利率（归母净利润÷营业总收入）"}

# 金额与它的"主语指标名"之间允许的最大字符距离。
# 没有这个约束，"吨价 314.4 万元"会被挂到远在句首的"茅台酒"上，又变误报。
MAX_NAME_GAP = 20


def _load_fields() -> dict:
    return yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))["fields"]


def _load_tolerance() -> dict:
    return yaml.safe_load(
        (ROOT / "rules" / "tolerance.yaml").read_text(encoding="utf-8"))


def _index_records(records: list[dict]) -> dict:
    """(item_code, period) -> 记录。同一键多条时保留全部（跨口径不混是一致性前提）。"""
    idx: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        idx.setdefault((r["item_code"], r["period"]), []).append(r)
    return idx


def margin_alt_truth(code: str, period: str, idx: dict) -> Decimal | None:
    """算出同一期间的**另一净利率口径**真值（见 MARGIN_ALT）；不适用或数据不全返回 None。

    真值同样由程序从结构化数据算，不让模型参与；精度与 derived 一致（百分数保留一位小数）。
    """
    alt = MARGIN_ALT.get(code)
    parts = MARGIN_PARTS.get(alt) if alt else None
    if not parts:
        return None
    num_recs, den_recs = idx.get((parts[0], period)), idx.get((parts[1], period))
    if not num_recs or not den_recs:
        return None
    try:
        num = Decimal(str(num_recs[0]["value"]).replace(",", "").strip())
        den = Decimal(str(den_recs[0]["value"]).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if den == 0:
        return None
    return (num / den * 100).quantize(Decimal("0.1"))


def _fy_index(records: list[dict]) -> dict:
    """(item_code, 年份) -> 数值：只收期间为纯 4 位年份的时期数记录，供同比基准使用。

    刻意不收时点（2025-12-31）：时点科目没有"同比"这一说，混进来会算出错误的基准。
    """
    out: dict[tuple[str, int], object] = {}
    for r in records:
        m = re.match(r"^(20\d{2})$", str(r.get("period") or ""))
        if m:
            out[(r["item_code"], int(m.group(1)))] = r.get("value")
    return out


def yoy_truth(code: str, period: str, fy: dict) -> Decimal | None:
    """财报口径同比：(本期−上期)÷|上期|×100。缺上期或分母为零返回 None——不猜、不补。

    只认**纯年份**期间：`2025-H1` / `2025-12-31` 的上期基数不是上一个年度数
    （前者应比 2024-H1、后者是时点数），拿年度数去算会把基期算错，故一律不产出。
    """
    m = re.match(r"^(20\d{2})$", str(period or ""))
    if not m:
        return None
    y = int(m.group(1))
    cur, prev = fy.get((code, y)), fy.get((code, y - 1))
    if cur is None or prev is None:
        return None
    try:
        c = Decimal(str(cur).replace(",", "").strip())
        p = Decimal(str(prev).replace(",", "").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if p == 0:
        return None
    return ((c - p) / abs(p) * 100).quantize(Decimal("0.01"))


# 同比容差单列：研报的增速常只保留一位小数（真值 -4.53% 写成 -4.5%），
# 沿用金额的 rel 档位会把这种"两位小数vs一位小数"的四舍五入差异判成异常。
YOY_ABS_TOL = Decimal("0.1")
YOY_REL_TOL = Decimal("0.05")


def yoy_tolerance(truth: Decimal) -> Decimal:
    return max(YOY_ABS_TOL, YOY_REL_TOL * abs(truth))


PERIOD_YEAR_RE = re.compile(r"(20\d{2})\s*年")
YOY_TOKEN_RE = re.compile(r"(?:同比\s*)?([+\-])\s*(\d[\d,]*(?:\.\d+)?)\s*%")
YOY_WINDOW = 15               # "同比±X%"必须紧跟在数值之后，超出窗口就不认（避免挂到别的指标）


def period_year_before(sentence: str, fig: dict) -> str | None:
    """取指标名**之前**最近的一个"20XX年"，作为该数值的期间修饰；没有则返回 None。

    「截至 2026年6月末合同负债 80.1 亿元」的期间修饰是 2026，而数值匹配的是 2025-12-31
    ——正是靠这一步才能把"只写错期间、金额没错"的引用抓出来。
    """
    pos = fig.get("name_pos")
    if pos is None:
        return None
    years = PERIOD_YEAR_RE.findall(sentence[:pos])
    return years[-1] if years else None


def yoy_after(sentence: str, fig: dict) -> tuple[Decimal, str] | None:
    """取数值之后紧跟的"同比 ±X%"（窗口内），返回 (带符号的百分数, 原串)；没有则 None。"""
    end = fig.get("num_end")
    if end is None:
        return None
    seg = sentence[end:end + YOY_WINDOW]
    m = YOY_TOKEN_RE.search(seg)
    if not m:
        return None
    try:
        val = Decimal(m.group(2).replace(",", ""))
    except InvalidOperation:
        return None
    if m.group(1) == "-":
        val = -val
    return val, seg[:m.end()]


def _display_names(fields: dict) -> dict:
    """标准科目编号 -> 中文正式名，用于在句子里定位指标。"""
    return {code: m.get("zh", code) for code, m in fields.items()}


QUARTER_RE = re.compile(r"Q[1-4]|第[一二三四]季度|上半年|中报|半年度")

# 预测/多列表格行：研报的盈利预测表被抽取器压成一行，列头年份在抽取时丢失，
# 典型形态是「毛利率 | 92.1% | 91.3% | 90.6% | 90.4% | 90.6%」。此时行内数值
# 与年份列无法对齐，拿"行内第一个数值"去比某一期财报必然张冠李戴——实测
# 92.1% 实为 2026E、22.0% 实为预测期，却被贴到 2025/2023/2024，凭空产出 4 条误报。
PIPE_RE = re.compile(r"\|")
NUM_TOKEN_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
YEAR_TOKEN_RE = re.compile(r"20\d{2}")
FORECAST_MARKER_RE = re.compile(r"预计|预测|展望|指引")


def is_forecast_row(sentence: str) -> bool:
    """判断句子是否为「无法对齐期间的预测行/多列表格行」。只用于把误报降级为复核。"""
    n = len(NUM_TOKEN_RE.findall(sentence))
    if len(PIPE_RE.findall(sentence)) >= 2 and n >= 3:
        return True                       # 表格行：整行同型数值，列头年份已丢失
    return (bool(FORECAST_MARKER_RE.search(sentence))
            and n >= 3
            and not YEAR_TOKEN_RE.search(sentence))   # 展望句：无年份可对齐


def _mentions(sentence: str, zh_map: dict) -> list[tuple[int, int, str]]:
    """找出句中所有指标名的出现区间 (start, end, item_code)，重叠时长名优先。"""
    cands = [(m.start(), m.end(), code)
             for name, code in ALIASES.items()
             for m in re.finditer(re.escape(name), sentence)]
    cands += [(m.start(), m.end(), code)
              for code, zh in zh_map.items()
              for m in re.finditer(re.escape(zh), sentence)]
    cands.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    chosen: list[tuple[int, int, str]] = []
    for s, e, code in cands:
        if any(not (e <= cs or s >= ce) for cs, ce, _ in chosen):
            continue                      # 与本区间重叠的短名丢弃
        chosen.append((s, e, code))
    return chosen


def associate_figures(sentence: str, zh_map: dict) -> dict[str, dict]:
    """把句中每个金额关联到**它前面最近的那个指标名**。

    为什么不能用"指标名之后第一个金额"：研报里常见
        "茅台酒 2024 年营收 1495.3 亿元"
    其中"茅台酒"与"营收"都在金额之前。若按"名字后第一个金额"，
    1495.3 亿会被同时算作 REVENUE，于是拿分部数去比总营收 —— 必然误报。
    按"最近的前置指标名"归属，1495.3 亿只归 REVENUE_MOUTAI。
    """
    mentions = _mentions(sentence, zh_map)
    out: dict[str, dict] = {}
    for m in NUM_RE.finditer(sentence):
        owner = None
        for s, e, code in mentions:
            if e <= m.start() and m.start() - e <= MAX_NAME_GAP:
                if owner is None or e > owner[1]:
                    owner = (s, e, code)
        if owner is None:
            continue
        try:
            val = Decimal(m.group(2).replace(",", ""))
        except InvalidOperation:
            continue
        if m.group(1) == "-":
            val = -val

        unit = m.group(3)
        if unit in UNIT_SCALE:
            value, bad_unit = val * UNIT_SCALE[unit], ""
        else:
            # 形如"亿万元"的畸形单位：按首个量级字估值以便比较，同时标记单位异常。
            # 用 .get 兜底——量级字表与 UNIT_SCALE 的键不保证完全对齐，
            # 直接索引会抛 KeyError 把整条流程打断（实测踩过 "千"）。
            head = next((u for u in ("万亿", "亿", "万", "千") if unit.startswith(u)), "元")
            value, bad_unit = val * UNIT_SCALE.get(head, Decimal("1")), unit

        # 同一指标在句中多次出现时，保留第一次（研报通常先给主口径）
        out.setdefault(owner[2], {
            "value": value,
            "raw": sentence[owner[0]:m.end()],
            "num_raw": m.group(2) + unit,
            "matched_name": sentence[owner[0]:owner[1]],
            "name_pos": owner[0],     # 指标名起点：供期间修饰短语比对（见 _period_year）
            "num_end": m.end(),       # 数值终点：供紧随其后的"同比±X%"定位
            "bad_unit": bad_unit,
        })
    return out


def associate_percent(sentence: str, zh_map: dict) -> dict[str, dict]:
    """比率类指标专用：把句中的百分比关联到它**前面最近**的指标名。

    口径与 associate_figures 一致（同名归属规则、同样的最大间隔约束），
    只是金额正则换成百分号正则、不做单位量级换算——比率以 % 为唯一单位。
    """
    mentions = _mentions(sentence, zh_map)
    out: dict[str, dict] = {}
    for m in PCT_RE.finditer(sentence):
        owner = None
        for s, e, code in mentions:
            if e <= m.start() and m.start() - e <= MAX_NAME_GAP:
                if owner is None or e > owner[1]:
                    owner = (s, e, code)
        if owner is None:
            continue
        try:
            val = Decimal(m.group(2).replace(",", ""))
        except InvalidOperation:
            continue
        if m.group(1) == "-":
            val = -val
        out.setdefault(owner[2], {
            "value": val,
            "raw": sentence[owner[0]:m.end()],
            "num_raw": m.group(2) + "%",
            "matched_name": sentence[owner[0]:owner[1]],
            "name_pos": owner[0],
            "num_end": m.end(),
            "bad_unit": "",
        })
    return out


def tolerance_for(profile: dict, left: Decimal, right: Decimal) -> Decimal:
    """有效容差 = max(abs 下限, rel × max(|左|,|右|))；REPORT_REF 不按单位取下限。"""
    abs_floor = Decimal(str(profile.get("abs", "0")))
    rel = Decimal(str(profile.get("rel", "0")))
    return max(abs_floor, rel * max(abs(left), abs(right)))


def classify_diff(claim_val: Decimal, truth: Decimal) -> tuple[str, str]:
    """确定性归因：不做主观猜测，按顺序检查倍数/期间/符号。"""
    if truth == 0:
        return "VALUE_ERROR", "财报值为零，无法做倍数归因"
    ratio = claim_val / truth
    for exp in range(-12, 13):
        if exp == 0:
            continue
        if abs(ratio - (Decimal(10) ** exp)) < abs(Decimal(10) ** exp) * Decimal("0.001"):
            return "UNIT_SCALE", f"研报值 ≈ 财报值 × 10^{exp}"
    if claim_val == -truth:
        return "SIGN_ERROR", "研报值与财报值符号相反"
    return "VALUE_ERROR", f"研报值 / 财报值 = {ratio:.4f}，非 10 的整数次幂"


def check(matches: list[dict], claims_by_id: dict, records: list[dict]) -> dict:
    """对每条已匹配的 claim 做数值比对，返回 interfaces.md §4.1 形态的结果。"""
    fields = _load_fields()
    zh_map = _display_names(fields)
    tol_cfg = _load_tolerance()
    profile = tol_cfg["profiles"][TOLERANCE_PROFILE]
    idx = _index_records(records)

    results, skipped = [], []
    for m in matches:
        cid, code, period = m.get("claim_id"), m.get("item_code"), m.get("period")
        base = {"rule_id": RULE_ID, "dimension": "C研", "claim_id": cid,
                "item_code": code, "period": period,
                "evidence": m.get("evidence", {})}

        # 缺科目/期间 → 无法比对，转人工复核（不猜）
        if not code or not period:
            skipped.append({**base, "status": "REVIEW",
                            "reason": "未匹配到科目或期间，无法比对"})
            continue

        recs = idx.get((code, period))
        if not recs:
            skipped.append({**base, "status": "REVIEW",
                            "reason": f"结构化数据中无 {code}@{period}，需数据侧确认"})
            continue

        claim = claims_by_id.get(cid, {})
        sentence = claim.get("sentence", "")
        rec = recs[0]

        # 预测/多列表格行：列头年份在抽取时丢失，行内数值与年份列无法对齐
        # （见 is_forecast_row）。此时硬判必然张冠李戴，转人工复核。
        # ★ 预测类指标（含"调整幅度"）本质上需要"上一版预测/一致预期"作为外部基线，
        #   本系统只用年报数据、不引外部预期（derived.py 铁律 1），故无法确定性核验，
        #   只能如实转人工复核，不猜、不补。
        if is_forecast_row(sentence):
            skipped.append({**base, "status": "REVIEW",
                            "reason": "研报为预测/多列表格行：列头年份已丢失，且预测类指标"
                                      "（含调整幅度）需上一版预测基线，无法确定性核验，"
                                      "需人工复核"})
            continue

        # 期间不许混（interfaces.md §1.5）：研报写的是单季/半年度数，财报记录是全年累计，
        # 两者不可比。直接判不可比而不是判错——否则每句"Q4实现…"都会变成一条误报。
        if QUARTER_RE.search(sentence) and rec.get("period_type") == "FY":
            skipped.append({**base, "status": "REVIEW",
                            "reason": "研报为单季/半年度口径，与年度累计不可比"})
            continue

        # 单条解析异常不得打断整体流程（与 llm 侧"失败转人工复核"一致）：
        # 某句研报措辞触发解析边界时，只丢这一条，其余照常比对。
        try:
            # 比率类（毛利率/净利率/资产负债率）用百分号解析，其余科目用金额解析
            figs = (associate_percent(sentence, zh_map) if code in RATIO_CODES
                    else associate_figures(sentence, zh_map))
        except Exception as e:                                  # noqa: BLE001
            skipped.append({**base, "status": "REVIEW",
                            "reason": f"句内金额解析异常（{type(e).__name__}: {e}），需人工复核"})
            continue
        fig = figs.get(code)
        if not fig:
            skipped.append({**base, "status": "REVIEW",
                            "reason": "句中该指标名下未定位到金额（或该指标非本句主语），需人工复核"})
            continue

        truth = Decimal(recs[0]["value"])
        diff = fig["value"] - truth
        tol = tolerance_for(profile, fig["value"], truth)
        value_ok = abs(diff) <= tol

        # D1 期间短语一致性：研报把期间写错、金额却取自正确的期，数值比对永远抓不到
        # （金额本来就没错）——「截至 2026年6月末合同负债80.1亿元」即是此类。期间修饰
        # 取指标名**之前**最近的"20XX年"（见 period_year_before）。
        # 分级：金额对得上 → 判定期间写错，FAIL；金额也不符 → 可能是模型的期间匹配本身
        # 有误，不作断言，转人工复核，避免把系统自身的匹配错误记成研报的错。
        period_year = period_year_before(sentence, fig)

        if fig["bad_unit"]:
            # 单位写法本身非法（如"亿万元"）。数值即便凑巧接近也不放行——
            # 这正是"单位错误"这一类要抓的东西，不能因为数值误差在容差内就判通过。
            ok, cause = False, "UNIT_SCALE"
            reason = f"单位写法非法：{fig['bad_unit']}"
        elif period_year is not None and not str(period).startswith(period_year):
            if value_ok:
                ok, cause = False, "PERIOD_MISMATCH"
                reason = (f"期间不符：研报表述为 {period_year} 年，财报取值期间为 {period}；"
                          f"金额本身一致（{truth}）")
            else:
                skipped.append({**base, "status": "REVIEW",
                                "reason": f"研报表述期间为 {period_year} 年，与匹配期间 "
                                          f"{period} 不一致且金额亦不符，疑为期间匹配错误，"
                                          f"需人工复核"})
                continue
        else:
            ok = value_ok
            cause, reason = ("", "") if ok else classify_diff(fig["value"], truth)
            if not ok:
                # 口径互认：换另一净利率口径复核一次（见 MARGIN_ALT）。研报口语的
                # "净利率"常写归母口径，与主口径（净利润÷营业总收入）本就不同值——
                # 差异属列报口径不同，不是引用错误，判 PASS 并注明依据。
                alt = margin_alt_truth(code, period, idx)
                if alt is not None and abs(fig["value"] - alt) <= tolerance_for(
                        profile, fig["value"], alt):
                    ok, cause = True, ""
                    truth = alt
                    diff = fig["value"] - truth
                    tol = tolerance_for(profile, fig["value"], truth)
                    reason = (f"口径差异：研报表述与「{MARGIN_ZH[MARGIN_ALT[code]]}」"
                              f"一致（{truth}），非引用错误")

        results.append({
            **base,
            "status": "PASS" if ok else "FAIL",
            "left": {"source": "research_report", "raw": fig["num_raw"],
                     "value": str(fig["value"])},
            "right": {"item_code": code, "period": period, "value": str(truth)},
            "diff": str(diff),
            "tolerance": str(tol),
            "severity": "MED" if ok else "HIGH",
            "cause": cause,
            "reason": reason,
            "matched_text": fig["raw"],
            "truth_page": recs[0].get("page"),
            "truth_chunk_id": recs[0].get("chunk_id"),
        })

        # D2 同比核验（独立规则 RC02）：数值之后紧跟的"同比±X%"是否与财报口径同比一致。
        # 只用于**金额类、纯年度期间**：比率类研报惯例用"个百分点"表述，基数与百分比不同，
        # 不在此规则判定；单季/半年度句（QUARTER_RE）的同比基期是同期而非上一年，同样不判。
        if (rec.get("period_type") == "FY" and code not in RATIO_CODES
                and not QUARTER_RE.search(sentence)):
            ya = yoy_after(sentence, fig)
            truth_yoy = yoy_truth(code, period, _fy_index(records))
            if ya is not None and truth_yoy is not None:
                yoy_val, yoy_raw = ya
                yoy_tol = yoy_tolerance(truth_yoy)
                if abs(yoy_val - truth_yoy) > yoy_tol:
                    yoy_diff = yoy_val - truth_yoy
                    yoy_base = {**base, "rule_id": YOY_RULE_ID,
                                "left": {"source": "research_report", "raw": yoy_raw,
                                         "value": str(yoy_val)},
                                "right": {"item_code": code, "period": period,
                                          "value": str(truth_yoy)},
                                "diff": str(yoy_diff), "tolerance": str(yoy_tol),
                                "matched_text": yoy_raw}
                    # 符号相反而幅度相当 → 方向写错（如真值 -1.2% 写成 +1.2%），可断言；
                    # 仅幅度不符 → 可能是口径/四舍五入，转人工复核，不作断言。
                    if ((yoy_val > 0) != (truth_yoy > 0)
                            and abs(abs(yoy_val) - abs(truth_yoy)) <= yoy_tol):
                        results.append({**yoy_base, "status": "FAIL", "severity": "HIGH",
                                        "cause": "SIGN_ERROR",
                                        "reason": f"同比方向错误：研报为 {yoy_raw}，"
                                                  f"财报口径同比为 {truth_yoy}%"})
                    else:
                        skipped.append({**yoy_base, "status": "REVIEW",
                                        "reason": f"研报同比 {yoy_raw} 与财报口径同比 "
                                                  f"{truth_yoy}% 不符，需人工复核"})
    return {"results": results, "review": skipped}


def dedup_claim_matches(matches: list[dict]) -> tuple[list[dict], list[dict]]:
    """同一指标在同一句里被枚举多个期间时**去多义化**。返回 (保留, 转人工复核)。

    一句研报引用通常只对应一个期间。模型偶尔会对同一指标枚举多个期间（实测：一句
    「资产负债率 | 22.0% | 19.5% | …」被展开成 2023/2024/2025 三个期间，同一数值
    被拿去比三年的财报，凭空产出 3 条互相矛盾的「异常」）。规则：

      - 只保留 period 中的年份在句子里**显式出现**的那几条；
      - 句中没有任何年份可对应 → 说明列头年份在抽取时已丢失，整组转人工复核，
        不用"行内第一个数值"去强行比对；
      - 不同指标（同句多指标各一条）不受影响，只有同名指标的重复期间才去重。
    """
    sent_by_id: dict[str, str] = {}
    for m in matches:
        sent_by_id.setdefault(m.get("claim_id"),
                              (m.get("evidence") or {}).get("sentence") or "")
    by_claim: dict[str, list[dict]] = {}
    for m in matches:
        by_claim.setdefault(m.get("claim_id"), []).append(m)

    kept: list[dict] = []
    demoted: list[dict] = []
    for cid, ms in by_claim.items():
        sentence = sent_by_id.get(cid) or ""
        years = set(YEAR_TOKEN_RE.findall(sentence))
        by_code: dict[str, list[dict]] = {}
        for m in ms:
            by_code.setdefault(m.get("item_code") or "", []).append(m)
        for code, group in by_code.items():
            if len(group) == 1:
                kept.extend(group)
                continue
            hit = [m for m in group
                   if set(YEAR_TOKEN_RE.findall(str(m.get("period") or ""))) & years]
            if hit:
                kept.extend(hit)
            else:
                demoted.append({
                    "claim_id": cid, "item_code": code, "period": None,
                    "reason": "同句命中多个期间且句中无对应年份，期间无法对齐，转人工复核",
                    "sentence": sentence,
                })
    return kept, demoted


def run_research_check(claims_path: str, matches: list[dict],
                       records: list[dict], run_id: str,
                       unmatched: list[dict] | None = None) -> dict:
    """供 pipeline 调用的入口：读研报原文、比对、落盘。

    ``unmatched``：模型压根没作答的 claim（典型是离线缓存未命中）。必须一并记成
    「待人工复核」——否则这批引用在报告与网页里凭空消失，读者会以为它们已被核查。
    """
    claims = json.loads((ROOT / claims_path).read_text(encoding="utf-8"))
    by_id = {c["claim_id"]: c for c in claims}
    out = check(matches, by_id, records)
    for u in unmatched or []:
        c = by_id.get(u.get("claim_id")) or {}
        out["review"].append({
            "rule_id": RULE_ID, "dimension": "C研", "claim_id": u.get("claim_id"),
            "item_code": u.get("item_code"), "period": u.get("period"),
            "status": "REVIEW",
            "reason": u.get("reason") or "模型未返回该条的匹配结果",
            "evidence": {"file": c.get("source"), "section": c.get("section"),
                         "sentence": u.get("sentence") or c.get("sentence")},
        })
    p = ROOT / "runs" / run_id / "rules" / "research_claims.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True),
                 encoding="utf-8")
    return out
