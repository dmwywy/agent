# 返回校验模块
# src/validators.py
import re

def check_enum_whitelist(obj, allowed: dict) -> list[str]:
    """枚举白名单：canonical_item / item_code / cause 只能在候选集合里"""
    errs = []
    if isinstance(obj, dict) and "items" in obj:
        for it in obj["items"]:
            if it.get("canonical_item") and it["canonical_item"] not in allowed["item_codes"]:
                errs.append(f"canonical_item 越界：{it['canonical_item']}")
    if isinstance(obj, dict) and "matches" in obj:
        for m in obj["matches"]:
            if m.get("item_code") not in allowed["item_codes"]:
                errs.append(f"item_code 越界：{m.get('item_code')}")
    if isinstance(obj, dict) and "cause" in obj:
        if obj["cause"] not in allowed["causes"]:
            errs.append(f"cause 越界：{obj['cause']}")
    return errs

def check_no_amounts(text: str, allowed_numbers: set[str]) -> list[str]:
    """★ 报告文字里出现的每个数字，都必须在程序给定的白名单里"""
    errs = []
    for num in re.findall(r"\d[\d,]*\.?\d*", text):
        clean = num.replace(",", "")
        if clean not in allowed_numbers:
            errs.append(f"报告中出现未经程序确认的数字：{num}")
    return errs

def check_evidence(ev: dict, chunks: dict[str, str]) -> tuple[bool, str]:
    """证据校验：chunk_id 必须在本次输入内，quote 必须真实存在于该片段"""
    cid, quote = ev.get("chunk_id"), ev.get("quote", "")
    if cid not in chunks:
        return False, f"chunk_id 不在本次输入中（疑似编造）：{cid}"
    if quote and quote not in chunks[cid]:
        return False, "原文片段在该片段中不存在（疑似编造）"
    return True, ""