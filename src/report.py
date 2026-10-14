# src/report.py
"""核查报告生成：PDF / Excel / 网页三种形式。

内容按《项目思路与实施流程》第八步的 13 项组织：
  1 项目基本信息  2 核查材料范围  3 数据提取结果  4 规则校验结果  5 异常问题清单
  6 原文证据及页码  7 差异计算过程  8 风险提示  9 修改建议  10 人工复核记录
  11 提取准确率与分项指标  12 CSI 公式/权重/分级  13 各类规则数量统计

★ 首页必须含免责声明（interfaces.md §6.3(3)）：
  报告只呈现核查结果，不作审计结论、不构成投资建议。
★ 报告中的一切数值与页码均取自程序产物，本模块不做任何计算与改写。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from decimal import Decimal, InvalidOperation

ROOT = Path(__file__).resolve().parents[1]

DISCLAIMER = "本系统输出为财务数据一致性核查结果，不构成投资建议，所有结论需人工复核。"
REPORT_VERSION = "1.0"
STATUS_ZH = {"PASS": "通过", "FAIL": "异常", "REVIEW": "需人工复核", "NA": "不适用"}
DIM_ZH = {"C内": "表内勾稽", "C间": "表间一致", "C期": "跨期衔接",
          "C附": "主表与附注", "C研": "研报引用"}


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def collect(run_id: str) -> dict:
    """把一次运行的全部产物汇总成报告模型。缺哪份产物就少哪一节，不报错。"""
    base = ROOT / "runs" / run_id
    manifest = _read(base / "manifest.json", {}) or {}
    results = _read(base / "rules" / "results.json", {}) or {}
    csi = _read(base / "rules" / "csi.json", {}) or {}
    research = _read(base / "rules" / "research_claims.json", {}) or {}
    mappings = _read(base / "llm" / "mappings.json", {}) or {}
    commentary = _read(base / "llm" / "commentary.json", {}) or {}

    rule_results = results.get("results", [])
    # 模型解释单独存放（结果文件保持纯确定性），此处按编号关联回来仅供展示
    from src.explain import load_explanations
    explanations = load_explanations(run_id)
    for r in rule_results:
        e = explanations.get(r.get("rule_id"))
        if e:
            r["explanation"] = e
    for r in research.get("results", []):
        e = explanations.get(r.get("claim_id"))
        if e:
            r["explanation"] = e

    anomalies = [r for r in rule_results if r.get("status") in ("FAIL", "REVIEW")]
    research_fail = [r for r in research.get("results", []) if r.get("status") == "FAIL"]

    # 提取到的字段：现场解析目录优先，其次共享基线
    fields, ingested = [], base / "ingested"
    scan_dir = ingested if ingested.exists() else ROOT / "data" / "structured"
    for p in sorted(scan_dir.glob("*.json")) if scan_dir.exists() else []:
        payload = _read(p)
        if isinstance(payload, list):
            fields.extend(r for r in payload
                          if isinstance(r, dict) and "item_code" in r)

    model = {
        "run_id": run_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "report_version": REPORT_VERSION,
        "disclaimer": DISCLAIMER,
        "manifest": manifest,
        "results": results,
        "rule_results": rule_results,
        "anomalies": anomalies,
        "csi": csi,
        "research": research,
        "research_fail": research_fail,
        "mappings": mappings,
        "fields": fields,
        "commentary": commentary,
        "counts": _counts(rule_results, research),
    }
    # 成段结论：由已完成聚合的计数与比对结果生成，供三种报告共用同一份文案
    model["summary"] = build_summary(model)
    model["research_review"] = build_research_review(model)
    return model


def _counts(rule_results: list[dict], research: dict) -> dict:
    """第 13 项：各类规则的适用/通过/异常/待复核数量。"""
    per_dim: dict[str, dict] = {}
    for r in rule_results:
        d = per_dim.setdefault(r.get("dimension", "?"), {
            "applicable": 0, "pass": 0, "fail": 0, "review": 0, "na": 0})
        st = r.get("status")
        if st == "NA":
            d["na"] += 1
            continue
        d["applicable"] += 1
        d[{"PASS": "pass", "FAIL": "fail", "REVIEW": "review"}.get(st, "review")] += 1
    rc = research.get("results", [])
    return {
        "rules_total": len(rule_results),
        "rules_per_dim": per_dim,
        "research_total": len(rc),
        "research_pass": sum(1 for r in rc if r.get("status") == "PASS"),
        "research_fail": sum(1 for r in rc if r.get("status") == "FAIL"),
        "research_review": len(research.get("review", [])),
        "fields": 0,      # 由 caller 填
    }


def _dim_counts(counts: dict) -> tuple[int, int, int, int, int]:
    """把 rules_per_dim 的计数做展示性汇总：(可执行, 通过, 异常, 待复核, 不适用)。

    只汇总已存在的计数器，不重算任何财务数值——报告不承担计算职责。
    """
    per = counts.get("rules_per_dim") or {}
    return (sum(v["applicable"] for v in per.values()),
            sum(v["pass"] for v in per.values()),
            sum(v["fail"] for v in per.values()),
            sum(v["review"] for v in per.values()),
            sum(v["na"] for v in per.values()))


def build_summary(model: dict) -> list[str]:
    """核查总评：把已有的计数与 CSI 组织成**成段结论**，置于报告首页。

    ★ 只陈述 model 里已经存在的数字（counts / csi / manifest / fields），
      不新做任何计算与猜测：报告不产结论性判断，仅呈现程序侧的事实。
      全部由确定性文本模板生成，离线可复现，不增加模型调用。
    """
    c = model.get("counts") or {}
    applicable, passed, failed, review, na = _dim_counts(c)
    paras = [f"本次核查共提取结构化字段 {len(model.get('fields') or [])} 条，"
             f"执行规则 {c.get('rules_total', 0)} 条，其中可执行 {applicable} 条、"
             f"通过 {passed} 条、异常 {failed} 条、待人工复核 {review} 条、不适用 {na} 条。"]

    per = c.get("rules_per_dim") or {}
    hits = [(k, v["fail"]) for k, v in sorted(per.items()) if v.get("fail")]
    if hits:
        paras.append("异常集中在 " + "、".join(
            f"{DIM_ZH.get(k, k)} {n} 条" for k, n in hits) + "。")
    else:
        paras.append("未发现规则级异常：表内勾稽与表间比对在容差内自洽。")

    rc = (model.get("research") or {}).get("results") or []
    if rc:
        paras.append(f"研报引用核查共比对 {len(rc)} 条："
                     f"一致 {sum(1 for r in rc if r.get('status') == 'PASS')} 条、"
                     f"不一致 {sum(1 for r in rc if r.get('status') == 'FAIL')} 条、"
                     f"待人工复核 {len((model.get('research') or {}).get('review') or [])} 条。")
    else:
        paras.append("本批材料未包含研究报告，或研报引用未匹配到可比字段：研报引用维度不适用。")

    csi = model.get("csi") or {}
    if csi.get("csi") is not None:
        paras.append(f"综合一致性指数 CSI = {csi.get('csi')}，"
                     f"等级 {csi.get('grade')}（{csi.get('grade_label', '')}）。")
    else:
        paras.append("本次运行没有可执行的规则，未计算 CSI。")

    advice = []
    if failed:
        advice.append(f"建议优先人工核对上述 {failed} 条异常，回到原始披露确认差异是否属于"
                      f"列报口径、会计政策变更或四舍五入。")
    if review:
        advice.append(f"另有 {review} 条标记为待人工复核，程序无法自动判定，不代表存在错误。")
    if not failed and not review:
        advice.append("报告期内没有需要人工介入的条目；可结合分部与附注披露做进一步复核。")
    calls = (model.get("manifest") or {}).get("calls") or {}
    if calls.get("cache_miss") or calls.get("failed"):
        advice.append(f"离线运行下有 {calls.get('cache_miss', 0)} 条语义任务未命中缓存、"
                      f"{calls.get('failed', 0)} 条调用失败，已转入人工复核，"
                      f"在线重跑可补齐这部分语义匹配。")
    paras.append("处置建议：" + "".join(advice))
    paras.append("本报告仅反映材料内部的一致性与引用关系，不构成审计意见或投资建议。")
    return paras


def build_research_review(model: dict) -> list[str]:
    """研报观点与财报数字差异点评：把已有比对结果写成成段点评。

    ★ 数值一律取自 research_check 的产出（left/right/diff/tolerance/cause），
      本函数只做文字陈述，不重算、不改写。
    """
    research = model.get("research") or {}
    rc = research.get("results") or []
    if not rc:
        return []
    fails = [r for r in rc if r.get("status") == "FAIL"]
    passed = [r for r in rc if r.get("status") == "PASS"]
    review = research.get("review") or []
    paras = [f"本批材料共比对研报引用 {len(rc)} 条：一致 {len(passed)} 条、"
             f"不一致 {len(fails)} 条、待人工复核 {len(review)} 条。"]
    for r in fails:
        sentence = (r.get("evidence") or {}).get("sentence") or "（研报原句未记录）"
        left = r.get("left") or {}
        right = r.get("right") or {}
        paras.append(
            f"研报称「{sentence}」——该句把 {r.get('item_code')}（{r.get('period')}）"
            f"表述为 {left.get('raw') or left.get('value')}，而财报披露为 {right.get('value')}，"
            f"差异 {r.get('diff')}（容差 {r.get('tolerance')}）；"
            f"归因 {r.get('cause', '')}：{r.get('reason', '') or '程序未给出进一步归因'}。"
            f"建议核对引用数值、单位与期间，并按证据定位回研报原文确认。")
    if not fails:
        paras.append("研报引用与财报数字全部一致：本批比对未发现引用层面的不一致。")
    return paras


def _suggest(status: str, dimension: str, reason: str) -> str:
    """第 9 项：修改建议。按状态与维度给通用建议，不替人下财务结论。"""
    if status == "REVIEW":
        return f"请人工核对{reason or '该条涉及的原始披露'}后回填复核结论。"
    if dimension == "C研":
        return "请核对研报引用的数值、单位与期间是否与财报一致，并按证据定位回原始材料确认。"
    if dimension == "C期":
        return "请确认是否存在会计政策变更或追溯重述；若无，则需核对上期期末数。"
    return "请按证据定位回原始报表核对相关科目，确认差异是否属于列报口径差异。"


# ---------------- PDF ----------------

def render_pdf(model: dict, path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Title"], fontName="STSong-Light", fontSize=18)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontName="STSong-Light", fontSize=13,
                        spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", parent=base["BodyText"], fontName="STSong-Light",
                          fontSize=9.5, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.grey)
    # 表格单元格样式：独立于 body，行高紧凑，配合 Paragraph 在列宽内自动换行
    cell = ParagraphStyle("cell", parent=base["BodyText"], fontName="STSong-Light",
                          fontSize=8, leading=10)

    def tbl(rows, widths=None, header=True):
        # ★ 单元格一律用 Paragraph 渲染：纯字符串不会被 reportlab 换行，
        #   长科目名（如"处置固定资产、无形资产和其他长期资产的损失…"）会直接
        #   溢出到相邻列、字体重叠。Paragraph 会在列宽内自动断行，杜绝重叠。
        rows = [[Paragraph(_esc(c), cell) if isinstance(c, str) else c for c in row]
                for row in rows]
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F0F0")) if header else
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return t

    story = [Paragraph("财报一致性核查报告", h1),
             Paragraph(f"运行编号 {model['run_id']} ｜ 生成时间 {model['generated_at']}", small),
             Spacer(1, 4),
             Paragraph(f"<b>{DISCLAIMER}</b>", body),
             Spacer(1, 8)]

    # 核查总评：成段结论前置，让评审第一眼看到核查规模、异常分布与 CSI 等级
    story.append(Paragraph("核查总评", h2))
    for p in model.get("summary") or []:
        story.append(Paragraph(_esc(p), body))
    story.append(Spacer(1, 8))

    # 1 项目基本信息 / 2 材料范围
    m = model["manifest"]
    story.append(Paragraph("一、项目基本信息与核查材料范围", h2))
    story.append(tbl([
        ["项", "值"],
        ["运行编号", model["run_id"]],
        ["模型标识", (m.get("model") or {}).get("model_id", "—")],
        ["提示词哈希", "；".join(f"{p['prompt_id']}:{p['sha256'][:12]}"
                              for p in (m.get("prompts") or [])) or "—"],
        ["调用统计", json.dumps(m.get("calls", {}), ensure_ascii=False)],
        ["提取字段数", str(len(model["fields"]))],
        ["规则结果数", str(model["counts"]["rules_total"])],
    ], widths=[35 * mm, 130 * mm]))

    # 3 数据提取结果
    story.append(Paragraph("二、数据提取结果（部分）", h2))
    if model["fields"]:
        rows = [["科目", "期间", "数值", "定位"]]
        for r in model["fields"][:12]:
            loc = f"p{r['page']}" if r.get("page") is not None else (r.get("locator") or "—")
            rows.append([r.get("item_raw", r.get("item_code", "")), str(r.get("period", "")),
                         r.get("value", ""), loc])
        story.append(tbl(rows, widths=[46 * mm, 26 * mm, 48 * mm, 45 * mm]))
        if len(model["fields"]) > 12:
            story.append(Paragraph(f"（共 {len(model['fields'])} 条，此处仅列前 12 条；"
                                   f"完整清单见 Excel 版）", small))
    else:
        story.append(Paragraph("未提取到字段记录。", body))

    # 4/5/6/7 异常清单（含证据与差异过程）
    story.append(PageBreak())
    story.append(Paragraph("三、异常问题清单（含证据与差异计算过程）", h2))
    if model["anomalies"]:
        for i, a in enumerate(model["anomalies"], 1):
            ev = a.get("evidence") or {}
            loc = f"p{ev['page']}" if ev.get("page") is not None else (ev.get("locator") or "—")
            story.append(Paragraph(
                f"<b>异常 {i}｜{a.get('rule_id')}｜{DIM_ZH.get(a.get('dimension',''), a.get('dimension',''))}"
                f"｜{STATUS_ZH.get(a.get('status'), a.get('status'))}</b>", body))
            story.append(Paragraph(
                f"计算式：{a.get('expr') or '—'}<br/>"
                f"差异：{a.get('diff')}　容差：{a.get('tolerance')}　"
                f"严重程度：{a.get('severity', '—')}<br/>"
                f"证据：{ev.get('file') or '—'}　定位：{loc}　片段：{ev.get('raw_text') or '—'}<br/>"
                f"建议：{_suggest(a.get('status'), a.get('dimension',''), a.get('reason',''))}", small))
            story.append(Spacer(1, 5))
    else:
        story.append(Paragraph("本次运行未发现异常。", body))

    # 研报引用异常
    if model["research_fail"]:
        story.append(Paragraph("四、研报引用异常", h2))
        rows = [["科目", "期间", "研报值", "财报值", "差异", "归因"]]
        for r in model["research_fail"]:
            rows.append([r.get("item_code", ""), str(r.get("period", "")),
                         (r.get("left") or {}).get("raw", ""),
                         (r.get("right") or {}).get("value", "")[:18],
                         (r.get("diff") or "")[:18], f"{r.get('cause','')} {r.get('reason','')}"])
        story.append(tbl(rows, widths=[26 * mm, 20 * mm, 30 * mm, 34 * mm, 26 * mm, 29 * mm]))

    # 研报观点 vs 财报数字：成段点评（有研报比对结果时才出现）
    review = model.get("research_review") or []
    if review:
        story.append(Paragraph("研报观点与财报数字差异点评", h2))
        for p in review:
            story.append(Paragraph(_esc(p), body))
        story.append(Spacer(1, 6))

    # 大模型研报式评价（仅供参考）：数字均来自程序产物，经白名单校验
    comment = (model.get("commentary") or {}).get("text", "")
    if comment:
        story.append(Paragraph("大模型经营评价（仅供参考）", h2))
        story.append(Paragraph(_esc(comment), body))
        story.append(Paragraph("本段由模型基于程序提取数据生成，仅供参考，不构成投资建议。", small))
        story.append(Spacer(1, 6))

    # 11/12/13 量化评价
    story.append(PageBreak())
    story.append(Paragraph("五、一致性量化评价", h2))
    csi = model["csi"]
    if csi:
        rows = [["维度", "含义", "可执行", "通过", "异常", "待复核", "不适用", "得分"]]
        for k, v in sorted((csi.get("dimensions") or {}).items()):
            rows.append([k, DIM_ZH.get(k, ""), v["rules_applicable"], v["pass"], v["fail"],
                         v["review"], v["na"],
                         "不适用" if v["score"] is None else f"{v['score']*100:.1f}"])
        story.append(tbl(rows, widths=[16 * mm, 30 * mm, 18 * mm, 14 * mm, 14 * mm,
                                       18 * mm, 16 * mm, 18 * mm]))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<b>综合一致性指数 CSI = {csi.get('csi')}　等级 {csi.get('grade')}"
            f"（{csi.get('grade_label','')}）</b>", body))
        story.append(Paragraph(
            f"计算公式：CSI = Σ(维度得分 × 归一化权重) × 100；"
            f"维度得分 = (通过数 + 0.5 × 待复核数) ÷ 可执行规则数。<br/>"
            f"原权重：{json.dumps(csi.get('weights_used', {}), ensure_ascii=False)}<br/>"
            f"归一化权重：{json.dumps(csi.get('weights_normalized', {}), ensure_ascii=False)}<br/>"
            f"不适用维度：{','.join(csi.get('na_dimensions') or []) or '无'}　"
            f"（{csi.get('note','')}）", small))
    else:
        story.append(Paragraph("本次运行无可执行的规则，未计算 CSI。", body))

    # 8 风险提示 / 9 修改建议 / 10 人工复核
    story.append(Paragraph("六、风险提示与人工复核", h2))
    story.append(Paragraph(
        "1. 本报告仅反映材料内部的一致性与引用关系，<b>不构成审计意见</b>。<br/>"
        "2. 标记为「需人工复核」的条目表示程序无法确定，<b>不代表存在错误</b>。<br/>"
        "3. 差异可能源于列报口径、会计政策变更或四舍五入，须结合原始披露判断。<br/>"
        "4. 人工复核结论请在结果文件的 human_review 字段回填，报告将同时呈现机器初判与人工结论。",
        small))
    story.append(Spacer(1, 6))
    story.append(Paragraph("人工复核记录", h2))
    reviewed = [r for r in model["rule_results"] if r.get("human_review")]
    story.append(Paragraph(
        f"已回填复核结论 {len(reviewed)} 条；待复核 {model['counts']['rules_per_dim'] and sum(v['review'] for v in model['counts']['rules_per_dim'].values())} 条。",
        small))
    story.append(Spacer(1, 10))
    story.append(Paragraph(DISCLAIMER, small))

    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4,
                      leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm,
                      title=f"财报一致性核查报告 {model['run_id']}").build(story)
    return path


# ---------------- Excel ----------------

def render_xlsx(model: dict, path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    head_font = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="F0F0F0")

    def sheet(name, header, rows):
        ws = wb.create_sheet(name)
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
        for r in rows:
            ws.append(r)
        for i, _ in enumerate(header, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = 20
        ws.freeze_panes = "A2"
        return ws

    # 摘要
    ws = wb.active
    ws.title = "摘要"
    csi = model["csi"] or {}
    ws.append(["项", "值"])
    for c in ws[1]:
        c.font, c.fill = head_font, head_fill
    for k, v in [
        ("运行编号", model["run_id"]), ("生成时间", model["generated_at"]),
        ("模型标识", (model["manifest"].get("model") or {}).get("model_id", "—")),
        ("提取字段数", len(model["fields"])),
        ("规则结果数", model["counts"]["rules_total"]),
        ("CSI", csi.get("csi")), ("等级", f"{csi.get('grade','')} {csi.get('grade_label','')}"),
        ("声明", DISCLAIMER),
    ]:
        ws.append([k, v])
    # 核查总评 / 研报差异点评 / 大模型评价：成段文本整段落入单元格并自动换行，Excel 里也能直接读到结论
    comment = (model.get("commentary") or {}).get("text", "")
    for label, paras in (("核查总评", model.get("summary") or []),
                         ("研报差异点评", model.get("research_review") or []),
                         ("大模型经营评价(仅供参考)", [comment] if comment else [])):
        if not paras:
            continue
        ws.append([label, "\n".join(paras)])
        ws.cell(ws.max_row, 2).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 70

    # 异常清单
    sheet("异常清单", ["规则编号", "维度", "状态", "期间", "计算式", "差异", "容差",
                       "严重程度", "文件", "页码/定位", "原文片段", "修改建议"],
          [[r.get("rule_id"), DIM_ZH.get(r.get("dimension", ""), r.get("dimension")),
            STATUS_ZH.get(r.get("status"), r.get("status")), r.get("period", ""),
            r.get("expr", ""), r.get("diff"), r.get("tolerance"), r.get("severity"),
            (r.get("evidence") or {}).get("file"),
            (r.get("evidence") or {}).get("page")
            if (r.get("evidence") or {}).get("page") is not None
            else (r.get("evidence") or {}).get("locator"),
            (r.get("evidence") or {}).get("raw_text"),
            _suggest(r.get("status"), r.get("dimension", ""), r.get("reason", ""))]
           for r in model["anomalies"]])

    # 规则明细
    sheet("规则明细", ["规则编号", "维度", "状态", "期间", "期别", "差异", "容差", "表达式"],
          [[r.get("rule_id"), r.get("dimension"), r.get("status"), r.get("period", ""),
            r.get("period_type", ""), r.get("diff"), r.get("tolerance"), r.get("expr", "")]
           for r in model["rule_results"]])

    # 提取字段
    sheet("提取字段", ["科目编号", "原文科目", "期间", "期别", "数值", "单位",
                       "页码/定位", "来源片段"],
          [[r.get("item_code"), r.get("item_raw"), r.get("period"), r.get("period_type"),
            r.get("value"), r.get("unit_raw"),
            r.get("page") if r.get("page") is not None else r.get("locator"),
            r.get("raw_text")] for r in model["fields"]])

    # 研报引用
    rc = (model["research"] or {}).get("results", [])
    if rc:
        sheet("研报引用", ["状态", "科目", "期间", "研报原文值", "研报数值", "财报值",
                           "差异", "容差", "归因", "说明", "研报原句"],
              [[r.get("status"), r.get("item_code"), r.get("period"),
                (r.get("left") or {}).get("raw"), (r.get("left") or {}).get("value"),
                (r.get("right") or {}).get("value"), r.get("diff"), r.get("tolerance"),
                r.get("cause"), r.get("reason"), (r.get("evidence") or {}).get("sentence")]
               for r in rc])

    # 待复核
    sheet("待人工复核", ["类型", "编号/科目", "原因", "原文"],
          [["规则", r.get("rule_id"), r.get("reason", ""),
            (r.get("evidence") or {}).get("raw_text", "")] for r in model["rule_results"]
           if r.get("status") == "REVIEW"]
          + [["研报", x.get("claim_id", ""), x.get("reason", ""), x.get("sentence", "")]
             for x in (model["research"] or {}).get("review", [])])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ---------------- HTML ----------------

_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>财报一致性核查报告 {run_id}</title>
<style>
 body{{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:0;background:#f7f8fa;color:#222}}
 .wrap{{max-width:1100px;margin:0 auto;padding:24px}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px;
   border-left:4px solid #2d6cdf;padding-left:8px}}
 .meta{{color:#666;font-size:13px}} .warn{{background:#fff4e5;border:1px solid #ffd08a;
   padding:10px 12px;border-radius:6px;font-size:13px;margin:12px 0}}
 table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
 th,td{{border:1px solid #e3e6ea;padding:6px 8px;text-align:left;vertical-align:top}}
 th{{background:#f0f2f5}} .FAIL{{color:#c0392b;font-weight:600}}
 .REVIEW{{color:#b7791f}} .PASS{{color:#1e7a46}} .NA{{color:#888}}
 .csi{{font-size:18px;font-weight:700;margin:8px 0}}
 code{{background:#f0f2f5;padding:1px 4px;border-radius:3px;font-size:12px}}
</style>
<div class="wrap">
<h1>财报一致性核查报告</h1>
<div class="meta">运行编号 {run_id} ｜ 生成时间 {generated_at} ｜ 报告版本 {report_version}</div>
<div class="warn">{disclaimer}</div>
{sections}
</div></html>"""


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _table(header: list[str], rows: list[list]) -> str:
    out = ["<table><thead><tr>" + "".join(f"<th>{_esc(h)}</th>" for h in header) + "</tr></thead><tbody>"]
    for r in rows:
        cells = []
        for i, v in enumerate(r):
            cls = f' class="{_esc(v)}"' if i == 0 and str(v) in STATUS_ZH else ""
            cells.append(f"<td{cls}>{_esc(v)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(out) + "</tbody></table>"


def render_html(model: dict, path: Path) -> Path:
    sections = []
    m = model["manifest"]
    # 核查总评：成段结论置顶，便于评审直接读到核查规模、异常分布与 CSI 等级
    sections.append("<h2>核查总评</h2>" + "".join(
        f"<p>{_esc(p)}</p>" for p in (model.get("summary") or [])))
    sections.append("<h2>一、项目基本信息与材料范围</h2>" + _table(
        ["项", "值"],
        [["运行编号", model["run_id"]],
         ["模型标识", (m.get("model") or {}).get("model_id", "—")],
         ["提示词哈希", "；".join(f"{p['prompt_id']}:{p['sha256'][:12]}"
                              for p in (m.get("prompts") or [])) or "—"],
         ["调用统计", json.dumps(m.get("calls", {}), ensure_ascii=False)],
         ["提取字段数", len(model["fields"])],
         ["规则结果数", model["counts"]["rules_total"]]]))

    sections.append("<h2>二、异常问题清单（含证据与差异过程）</h2>")
    if model["anomalies"]:
        sections.append(_table(
            ["状态", "规则", "维度", "计算式", "差异", "容差", "证据定位", "原文片段", "修改建议"],
            [[STATUS_ZH.get(r.get("status"), r.get("status")), r.get("rule_id"),
              DIM_ZH.get(r.get("dimension", ""), r.get("dimension")),
              r.get("expr", ""), r.get("diff"), r.get("tolerance"),
              f"p{(r.get('evidence') or {}).get('page')}"
              if (r.get("evidence") or {}).get("page") is not None
              else ((r.get("evidence") or {}).get("locator") or "—"),
              (r.get("evidence") or {}).get("raw_text") or "—",
              _suggest(r.get("status"), r.get("dimension", ""), r.get("reason", ""))]
             for r in model["anomalies"]]))
    else:
        sections.append("<p>本次运行未发现异常。</p>")

    if model["research_fail"]:
        sections.append("<h2>三、研报引用异常</h2>" + _table(
            ["状态", "科目", "期间", "研报原文", "研报值", "财报值", "差异", "归因"],
            [[r.get("status"), r.get("item_code"), r.get("period"),
              (r.get("left") or {}).get("raw"), (r.get("left") or {}).get("value"),
              (r.get("right") or {}).get("value"), r.get("diff"),
              f"{r.get('cause','')} {r.get('reason','')}"]
             for r in model["research_fail"]]))

    # 研报观点 vs 财报数字：成段点评（有研报比对结果时才出现）
    review = model.get("research_review") or []
    if review:
        sections.append("<h2>研报观点与财报数字差异点评</h2>" + "".join(
            f"<p>{_esc(p)}</p>" for p in review))

    # 大模型研报式评价（仅供参考）
    comment = (model.get("commentary") or {}).get("text", "")
    if comment:
        sections.append("<h2>大模型经营评价（仅供参考）</h2>"
                        f"<p>{_esc(comment)}</p>"
                        "<p class=\"warn\">本段由模型基于程序提取数据生成，仅供参考，不构成投资建议。</p>")

    csi = model["csi"] or {}
    if csi:
        sections.append("<h2>四、一致性量化评价</h2>")
        sections.append(f'<div class="csi">CSI = {csi.get("csi")}　'
                        f'等级 {csi.get("grade")}（{csi.get("grade_label","")}）</div>')
        sections.append(_table(
            ["维度", "含义", "可执行", "通过", "异常", "待复核", "不适用", "得分"],
            [[k, DIM_ZH.get(k, ""), v["rules_applicable"], v["pass"], v["fail"], v["review"],
              v["na"], "不适用" if v["score"] is None else f"{v['score']*100:.1f}"]
             for k, v in sorted((csi.get("dimensions") or {}).items())]))
        sections.append(
            f'<p><code>CSI = Σ(维度得分 × 归一化权重) × 100</code>　'
            f'维度得分 = (通过 + 0.5×待复核) ÷ 可执行规则数<br/>'
            f'原权重 {_esc(json.dumps(csi.get("weights_used", {}), ensure_ascii=False))}　'
            f'归一化 {_esc(json.dumps(csi.get("weights_normalized", {}), ensure_ascii=False))}<br/>'
            f'不适用维度：{_esc(",".join(csi.get("na_dimensions") or []) or "无")}</p>')

    sections.append("<h2>五、风险提示与人工复核</h2>"
                    "<p>1. 本报告仅反映材料内部一致性与引用关系，<b>不构成审计意见</b>。<br/>"
                    "2. 「需人工复核」表示程序无法确定，<b>不代表存在错误</b>。<br/>"
                    "3. 差异可能源于列报口径、会计政策变更或四舍五入，须结合原始披露判断。</p>")

    html = _HTML.format(run_id=_esc(model["run_id"]),
                        generated_at=_esc(model["generated_at"]),
                        report_version=_esc(model["report_version"]),
                        disclaimer=_esc(DISCLAIMER),
                        sections="".join(sections))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def build_all(run_id: str, formats=("pdf", "xlsx", "html")) -> dict:
    """生成三种形式的报告，返回 {格式: 路径}。"""
    model = collect(run_id)
    model["counts"]["fields"] = len(model["fields"])
    out = ROOT / "runs" / run_id
    made = {}
    if "pdf" in formats:
        made["pdf"] = render_pdf(model, out / "report.pdf")
    if "xlsx" in formats:
        made["xlsx"] = render_xlsx(model, out / "report.xlsx")
    if "html" in formats:
        made["html"] = render_html(model, out / "report.html")
    return {k: str(v.relative_to(ROOT)) for k, v in made.items()}
