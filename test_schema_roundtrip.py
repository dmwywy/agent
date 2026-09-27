# test_schema_roundtrip.py  用真实模型输出验证 schema 校验路径
import json
from pathlib import Path
from src.llm import call
from src.schemas import UnitPeriodOut
from src.validators import check_evidence

PROMPT = """你是上市公司财报表头识别助手。请只输出 json 格式，不要输出任何解释文字。
输入包含 chunk_id、header_text（表头原文）与 candidates（候选答案表）。
请从 candidates 中为 unit（单位）、scope（合并/母公司口径）、period_type（期间类型）各选一个值。
禁止输出任何金额数字，禁止新增字段。
输出 json 样例：
{"ok":true,"unit":"万元","scope":"CONSOLIDATED","period_type":"BAL_END","confidence":0.9,
 "evidence":{"chunk_id":"p042_t1","quote":"单位：万元"}}
"""

payload = {
    "chunk_id": "p042_t1",
    "header_text": "合并资产负债表   单位：万元   2025年12月31日   2024年12月31日",
    "candidates": {
        "unit": ["元", "千元", "万元", "百万元", "亿元", "unknown"],
        "scope": ["CONSOLIDATED", "PARENT"],
        "period_type": ["FY", "YTD", "BAL_END", "BAL_BEG", "UNKNOWN"],
    },
}

data, status = call(PROMPT, payload, run_id="20260927_schema_check", user_id="member_e")
print("调用状态：", status)
print("原始返回：", json.dumps(data, ensure_ascii=False))

try:
    obj = UnitPeriodOut.model_validate(data)          # 真输出的 schema 校验
    print("[1] schema 校验：通过 ->", obj.unit, obj.scope, obj.period_type)
except Exception as e:
    print("[1] schema 校验：失败 ->", e)

chunks = {"p042_t1": payload["header_text"]}
ok, msg = check_evidence(data.get("evidence", {}), chunks)
print("[2] 证据校验：", "通过" if ok else f"失败 -> {msg}")