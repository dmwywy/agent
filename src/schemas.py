# 返回格式模块
# src/schemas.py
from pydantic import BaseModel, Field
from typing import Literal

class Evidence(BaseModel):
    chunk_id: str = Field(description="必须来自本次输入，不允许模型自行编造")
    quote: str = Field(default="", max_length=200, description="原文片段，需在输入文本中真实存在")

class ItemMap(BaseModel):
    raw_header: str
    canonical_item: str          # 只能填标准清单里的 code；清单外的值由程序判为无效
    confidence: float = Field(ge=0, le=1)

class MatchItemOut(BaseModel):
    ok: bool
    items: list[ItemMap] = []
    evidence: Evidence
    reason: str = ""

class UnitPeriodOut(BaseModel):
    ok: bool
    unit: Literal["元","千元","万元","百万元","亿元","unknown"]
    scope: Literal["CONSOLIDATED","PARENT"]
    period_type: Literal["FY","YTD","BAL_END","BAL_BEG","UNKNOWN"]
    confidence: float = Field(ge=0, le=1)
    evidence: Evidence

class ClaimMatch(BaseModel):
    claim_id: str
    item_code: str               # 只能填 fields.yaml 里的 code
    period: str = ""             # 如 FY2025 / H1_2025
    confidence: float = Field(ge=0, le=1)
    reason: str = ""

class MatchClaimOut(BaseModel):
    ok: bool
    matches: list[ClaimMatch] = []
    evidence: Evidence

class ExplainOut(BaseModel):
    ok: bool
    cause: str                   # 只能从 candidates 里选
    explanation: str = ""
    confidence: float = Field(ge=0, le=1)
    evidence: Evidence

# 报告文字：单独一个类，因为要额外做"数字白名单"校验
class ReportTextOut(BaseModel):
    ok: bool
    text: str

SCHEMA_BY_TASK = {
    "match_item":     MatchItemOut,
    "unit_period":    UnitPeriodOut,
    "match_claim":    MatchClaimOut,
    "explain_anomaly": ExplainOut,
    "gen_report_text": ReportTextOut,
}