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
    """科目归一化：模型只做「原文科目名 → 标准科目编号」的语义映射。
    本任务不含 evidence：科目在财报中的位置（page/chunk_id/raw_text）由程序
    从数字经济A 的解析记录确定性回填，见 src/pipeline._attach_evidence。
    依据 interfaces.md §5.3 禁则3「模型不生成页码」——让模型复述 chunk_id 无信息增益，
    反而增加"抄错导致误杀正确映射"的路径。"""
    ok: bool = True
    items: list[ItemMap] = []
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
    """研报引用匹配：模型只判断「这句话在说哪个科目、哪个期间」，不判断数值对错。
    本任务不含 evidence，理由与 match_item 同源：一批最多 40 条 claim，
    单个 evidence 字段无法覆盖；且引用位置（研报文件名+章节+原句）本就是程序输入，
    让模型复述没有信息增益。位置由 pipeline._attach_claim_evidence 确定性回填。
    每条 claim 的语义依据由 ClaimMatch.reason 承载。"""
    ok: bool = True
    matches: list[ClaimMatch] = []

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