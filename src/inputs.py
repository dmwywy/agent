# 输入文本格式模块
# src/inputs.py
"""把 A 的解析结果包装成模型输入。本文件只做"包装"，不做任何数值计算。"""

import re
from decimal import Decimal, InvalidOperation

MAX_CHARS = 4000          # 单段文本上限
MAX_ITEMS = 40            # 一次最多让模型处理的科目/条款条数

# ---------- 科目归一化：给科目名，问它对应哪个标准科目 ----------
def build_match_item(headers: list[str], standard_items: list[dict]) -> dict:
    assert headers, "headers 不能为空"
    assert len(headers) <= MAX_ITEMS, f"一次最多 {MAX_ITEMS} 条，请分批"
    return {
        "task": "match_item",
        "headers": headers[:MAX_ITEMS],
        "standard_items": standard_items,   # [{"code":"REVENUE","name":"营业收入"}, ...]
    }

# ---------- 单位/期间/口径识别：只给表头文字，让它选，不给它自由发挥 ----------
def build_unit_period(chunk_id: str, header_text: str,
                      candidates: dict[str, list[str]]) -> dict:
    """header_text 是表头那一行原文；candidates 是候选答案表"""
    return {
        "task": "unit_period",
        "chunk_id": chunk_id,                # ★ 它只能引用这个 id，不能自己说页码
        "header_text": header_text[:MAX_CHARS],
        "candidates": candidates,            # {"unit":[...], "scope":[...], "period_type":[...]}
    }

# ---------- 研报引用匹配：判断"这句话在说哪个指标"，不判断对错 ----------
def build_match_claim(claims: list[dict], fields: list[dict]) -> dict:
    """claims: [{"claim_id":"c01","sentence":"...30字内..."}]
       fields: [{"item_code":"REVENUE","period":"2025"}]  ← 只给科目和期间，不给金额"""
    safe_claims = [{"claim_id": c["claim_id"], "sentence": c["sentence"][:500]}
                   for c in claims[:MAX_ITEMS]]
    return {"task": "match_claim", "claims": safe_claims, "fields": fields}

# ---------- 异常原因解释：把候选原因给它，让它做选择题 ----------
def build_explain_anomaly(rule_id: str, diff_desc: str, chunk_id: str,
                          quote: str, candidates: list[str]) -> dict:
    return {
        "task": "explain_anomaly",
        "rule_id": rule_id,
        "diff_desc": diff_desc,        # 程序生成的"差异有多大"的自然语言，不含明细数值
        "chunk_id": chunk_id,
        "quote": quote[:200],
        "candidates": candidates,      # ["UNIT_SCALE","PERIOD_MISMATCH","SCOPE_MIXED","..."]
    }

# ---------- 核查报告结论文字：只给"结构"，数字由程序在模板里填 ----------
MAX_ANOMALY_ITEMS = 30

def build_gen_report_text(dimensions: dict, anomalies: list[dict],
                          grade_label: str) -> dict:
    """报告结论文字。模型只写定性表述，**不产出任何数字**。

    dimensions: {"C内": {"status":"HAS_ISSUES"}, ...}
                只传定性标记，不把结果计数交给模型。
    anomalies:  [{"rule_id":"BS01","dimension":"C内","item_code":"TOTAL_ASSETS",
                  "status":"FAIL"}, ...]
    grade_label: 分级中文名，如"基本一致"（由程序按 CSI 判定）
    """
    safe = [{
        "rule_id": a.get("rule_id", ""),
        "dimension": a.get("dimension", ""),
        "item_code": a.get("item_code", ""),   # 只给科目编号，**不给金额与差额**
        "status": a.get("status", ""),
    } for a in anomalies[:MAX_ANOMALY_ITEMS]]

    safe_dimensions = {}
    for name, counts in dimensions.items():
        has_issue = bool(counts.get("fail", 0) or counts.get("review", 0))
        safe_dimensions[name] = {"status": "HAS_ISSUES" if has_issue else "CLEAR"}

    return {
        "task": "gen_report_text",
        "dimensions": safe_dimensions,
        "anomaly_items": safe,
        "grade_label": grade_label,
        "has_omitted_anomalies": len(anomalies) > MAX_ANOMALY_ITEMS,
    }


# ---------- 研报式评价：程序把确定数字喂给模型，模型写分析话，数字白名单校验 ----------
MAX_COMMENT_METRICS = 24     # 最多喂多少条核心经营指标，控制篇幅与成本
MAX_COMMENT_ANOMALIES = 20
MAX_COMMENT_RESEARCH = 20

# 供评价聚焦的核心科目（与研报点评最相关的收入/利润/现金流/资产负债主项）
COMMENT_CORE = ("TOTAL_REVENUE", "REVENUE", "NP_PARENT", "NET_PROFIT",
                "OPERATING_COST", "CF_OPERATING", "TOTAL_ASSETS", "TOTAL_LIAB",
                "TOTAL_EQUITY", "REVENUE_MOUTAI", "REVENUE_SERIES")


def _normalize_number(s: str) -> set[str]:
    """把一个数字串展开成多个**数值等价**的字符串写法，避免被同等数值不同写法误拦截。

    例："174140000000.00" → {"174140000000.00", "174140000000"}，
        "87.50"          → {"87.50", "87.5"}。
    括大的是**写法变体**，不是新数值——模型仍无法编造任何它没见过的数量。
    """
    clean = s.replace(",", "")
    out = {clean}
    try:
        d = Decimal(clean)
        if d == d.to_integral_value():
            out.add(str(d.to_integral_value()))
        else:
            out.add(format(d.normalize(), "f"))
    except InvalidOperation:
        pass
    return out


def collect_allowed_numbers(obj) -> set[str]:
    """从要喂给模型的 payload 里抽**程序已确认的全部数字**，去逗号后作为白名单。

    模型评价文本中出现的每个数字都必须落在这个集合里——它只能引用程序给它的数，
    不能自己编一个（如把它没看到的"毛利率=92%"算出来）。
    """
    nums = set()
    if isinstance(obj, dict):
        for v in obj.values():
            nums |= collect_allowed_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            nums |= collect_allowed_numbers(v)
    elif isinstance(obj, (int, float)):
        s = f"{obj:g}" if isinstance(obj, float) else str(obj)
        nums |= _normalize_number(s)
    elif isinstance(obj, str):
        for m in re.findall(r"\d[\d,]*\.?\d*", obj):
            nums |= _normalize_number(m)
    return nums


def build_gen_commentary(metrics: list[dict], csi: dict, anomalies: list[dict],
                         research_fail: list[dict], grade_label: str) -> dict:
    """研报式评价输入：把所有"程序已算好的确定数字"喂给模型，让它写一段分析评价。

    metrics: [{"code","name","period","value"}] —— 程序提取的经营指标值（带确定数值）
    csi:     {"csi": 87.5, "grade": "A", "grade_label": "基本一致"}
    anomalies:   异常清单（含确定 diff / tolerance）
    research_fail: 研报引用比对不一数（含研报值/财报值/差异）

    ★ 与 gen_report_text 相反：这里**给数字**，因为用户要的是"根据数据做评价"。
    但数字一律来自程序产物，模型只负责组织语言、不比对新数；输出再经 collect_allowed_numbers
    白名单兜底，任何程序没给的数字都会被拦截（src/tasks._post_checks）。
    """
    core = [{
        "code": m.get("code", ""),
        "name": m.get("name", m.get("code", "")),
        "period": str(m.get("period", "")),
        "value": str(m.get("value", "")),
    } for m in metrics if m.get("code") in COMMENT_CORE][:MAX_COMMENT_METRICS]

    safe_anom = [{
        "dimension": a.get("dimension", ""),
        "item_code": a.get("item_code", ""),
        "status": a.get("status", ""),
        "diff": str(a.get("diff", "")),
        "tolerance": str(a.get("tolerance", "")),
    } for a in anomalies[:MAX_COMMENT_ANOMALIES]]

    safe_research = [{
        "item_code": r.get("item_code", ""),
        "period": str(r.get("period", "")),
        "report_value": str((r.get("left") or {}).get("value", "")),
        "truth_value": str((r.get("right") or {}).get("value", "")),
        "diff": str(r.get("diff", "")),
        "cause": r.get("cause", ""),
    } for r in research_fail[:MAX_COMMENT_RESEARCH]]

    return {
        "task": "gen_commentary",
        "metrics": core,
        "csi": {"csi": csi.get("csi"),
                "grade": csi.get("grade", ""),
                "grade_label": grade_label},
        "anomalies": safe_anom,
        "research": safe_research,
        # ★ 显式给出各清单条数：模型可引用"有 N 条…存在差异"这类**程序已知的计数**，
        #   数字白名单据此放行（不得自行推算任何新数）。
        "counts": {
            "metrics": len(core),
            "anomalies": len(safe_anom),
            "research": len(safe_research),
        },
    }
