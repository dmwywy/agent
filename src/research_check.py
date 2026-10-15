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


def _display_names(fields: dict) -> dict:
    """标准科目编号 -> 中文正式名，用于在句子里定位指标。"""
    return {code: m.get("zh", code) for code, m in fields.items()}


QUARTER_RE = re.compile(r"Q[1-4]|第[一二三四]季度|上半年|中报|半年度")


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

        if fig["bad_unit"]:
            # 单位写法本身非法（如"亿万元"）。数值即便凑巧接近也不放行——
            # 这正是"单位错误"这一类要抓的东西，不能因为数值误差在容差内就判通过。
            ok, cause = False, "UNIT_SCALE"
            reason = f"单位写法非法：{fig['bad_unit']}"
        else:
            ok = abs(diff) <= tol
            cause, reason = ("", "") if ok else classify_diff(fig["value"], truth)

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
    return {"results": results, "review": skipped}


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
