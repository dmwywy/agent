# 输入文本格式模块
# src/inputs.py
"""把 A 的解析结果包装成模型输入。本文件只做"包装"，不做任何数值计算。"""
from typing import Any

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
       fields: [{"item_code":"REVENUE","period":"FY2025"}]  ← 只给科目和期间，不给金额"""
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