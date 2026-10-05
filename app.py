# app.py —— 网页入口
"""财报一致性量化核查智能体 · 网页界面。

运行：streamlit run app.py

界面只读 runs/<run_id>/ 下的产物，**不重跑流程、不调模型**——展示与计算分离，
看到的永远是已经落盘、可复核的结果。
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
STATUS_ZH = {"PASS": "通过", "FAIL": "异常", "REVIEW": "需人工复核", "NA": "不适用"}
DIM_ZH = {"C内": "表内勾稽", "C间": "表间一致", "C期": "跨期衔接",
          "C附": "主表与附注", "C研": "研报引用"}
DISCLAIMER = "本系统输出为财务数据一致性核查结果，不构成投资建议，所有结论需人工复核。"

st.set_page_config(page_title="财报一致性核查", layout="wide")


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def list_runs() -> list[str]:
    d = ROOT / "runs"
    if not d.exists():
        return []
    return sorted((p.name for p in d.iterdir() if p.is_dir()), reverse=True)


st.title("财报一致性量化核查智能体")
st.caption("财务报告信息一致性量化核查与研究报告纠错 · 结果视图")
st.warning(DISCLAIMER)

runs = list_runs()
if not runs:
    st.info("还没有任何运行结果。请先在命令行执行：\n\n"
            "```\npython -m fincheck ingest --from \"你的材料文件夹\" --run-id 我的测试\n```\n\n"
            "然后刷新本页面。")
    st.stop()

run_id = st.sidebar.selectbox("选择运行编号", runs)
base = ROOT / "runs" / run_id
manifest = _read(base / "manifest.json", {}) or {}
results = (_read(base / "rules" / "results.json", {}) or {}).get("results", [])
csi = _read(base / "rules" / "csi.json", {}) or {}
research = _read(base / "rules" / "research_claims.json", {}) or {}
mappings = (_read(base / "llm" / "mappings.json", {}) or {}).get("mappings", [])

fields = []
for p in sorted((base / "ingested").glob("*.json")) if (base / "ingested").exists() \
        else sorted((ROOT / "data" / "structured").glob("*.json")):
    payload = _read(p)
    if isinstance(payload, list):
        fields.extend(r for r in payload if isinstance(r, dict) and "item_code" in r)

# ---- 顶部指标卡 ----
calls = manifest.get("calls", {})
anomalies = [r for r in results if r.get("status") in ("FAIL", "REVIEW")]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("CSI", csi.get("csi") if csi.get("csi") is not None else "—",
          csi.get("grade_label", ""), delta_color="off")
c2.metric("提取字段", len(fields))
c3.metric("规则结果", len(results))
c4.metric("异常 / 待复核", len(anomalies))
c5.metric("模型调用", f"{calls.get('api_ok', 0)} 在线 / {calls.get('cache_hit', 0)} 缓存")

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["① 异常清单", "② 一致性指标", "③ 提取字段", "④ 规则明细", "⑤ 报告下载"])

# ---- ① 异常清单 ----
with tab1:
    if not anomalies:
        st.success("本次运行未发现异常。")
    else:
        for r in anomalies:
            ev = r.get("evidence") or {}
            loc = f"p{ev['page']}" if ev.get("page") is not None else (ev.get("locator") or "—")
            icon = "🔴" if r.get("status") == "FAIL" else "🟡"
            with st.expander(f"{icon} {r.get('rule_id')}｜"
                             f"{DIM_ZH.get(r.get('dimension'), r.get('dimension'))}｜"
                             f"{STATUS_ZH.get(r.get('status'), r.get('status'))}"
                             f"｜{r.get('period', '')}"):
                st.markdown(f"**计算式**：`{r.get('expr') or '—'}`")
                a, b, c = st.columns(3)
                a.metric("差异", r.get("diff") or "—")
                b.metric("容差", r.get("tolerance") or "—")
                c.metric("严重程度", r.get("severity") or "—")
                if r.get("reason"):
                    st.write(f"**原因**：{r['reason']}")
                st.write(f"**证据定位**：{ev.get('file') or '—'} · {loc}")
                if ev.get("raw_text"):
                    st.code(ev["raw_text"], language=None)

    rf = [x for x in research.get("results", []) if x.get("status") == "FAIL"]
    if rf:
        st.subheader("研报引用异常")
        st.dataframe(pd.DataFrame([{
            "状态": x.get("status"), "科目": x.get("item_code"), "期间": x.get("period"),
            "研报原文值": (x.get("left") or {}).get("raw"),
            "财报值": (x.get("right") or {}).get("value"),
            "差异": x.get("diff"), "归因": x.get("cause"), "说明": x.get("reason"),
        } for x in rf]), width='stretch')

# ---- ② 一致性指标 ----
with tab2:
    dims = csi.get("dimensions") or {}
    if not dims:
        st.info("本次运行没有可执行的规则，未计算 CSI。")
    else:
        rows = []
        for k, v in sorted(dims.items()):
            rows.append({
                "维度": k, "含义": DIM_ZH.get(k, ""),
                # 统一转成字符串：该列会混"数字"与"不适用"，列类型不一致时
                # Streamlit 底层的 pyarrow 会抛 ArrowInvalid（虽被吞掉，但表格渲染异常）
                "得分": "不适用" if v["score"] is None else f"{v['score'] * 100:.1f}",
                "可执行": v["rules_applicable"], "通过": v["pass"],
                "异常": v["fail"], "待复核": v["review"], "不适用": v["na"],
            })
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        st.markdown(
            f"**综合一致性指数 CSI = {csi.get('csi')}　"
            f"等级 {csi.get('grade')}（{csi.get('grade_label')}）**\n\n"
            f"- 公式：`CSI = Σ(维度得分 × 归一化权重) × 100`\n"
            f"- 维度得分 = `(通过数 + 0.5 × 待复核数) ÷ 可执行规则数`\n"
            f"- 原权重：`{json.dumps(csi.get('weights_used', {}), ensure_ascii=False)}`\n"
            f"- 归一化权重：`{json.dumps(csi.get('weights_normalized', {}), ensure_ascii=False)}`\n"
            f"- 不适用维度：`{','.join(csi.get('na_dimensions') or []) or '无'}`（权重已分摊）")

        scored = {k: v["score"] for k, v in dims.items() if v["score"] is not None}
        if scored:
            st.bar_chart(pd.DataFrame({"得分": scored}))

# ---- ③ 提取字段 ----
with tab3:
    if not fields:
        st.info("没有提取到字段记录。")
    else:
        df = pd.DataFrame([{
            "科目编号": r.get("item_code"), "原文科目": r.get("item_raw"),
            "期间": r.get("period"), "期别": r.get("period_type"),
            "数值": r.get("value"), "单位": r.get("unit_raw"),
            "定位": r.get("page") if r.get("page") is not None else r.get("locator"),
            "原文片段": r.get("raw_text"),
        } for r in fields])
        st.caption(f"共 {len(df)} 条。可按列排序、筛选。")
        st.dataframe(df, width='stretch', hide_index=True)
        st.download_button("下载为 CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{run_id}_fields.csv", mime="text/csv")

# ---- ④ 规则明细 ----
with tab4:
    if not results:
        st.info("没有规则核查结果。")
    else:
        df = pd.DataFrame([{
            "状态": STATUS_ZH.get(r.get("status"), r.get("status")),
            "规则编号": r.get("rule_id"), "维度": r.get("dimension"),
            "期间": r.get("period"), "期别": r.get("period_type"),
            "表达式": r.get("expr"), "差异": r.get("diff"), "容差": r.get("tolerance"),
            "原因": r.get("reason"),
        } for r in results])
        picks = st.multiselect("筛选状态", sorted(df["状态"].unique()),
                               default=sorted(df["状态"].unique()))
        st.dataframe(df[df["状态"].isin(picks)], width='stretch', hide_index=True)

    if mappings:
        with st.expander(f"科目归一化结果（{len(mappings)} 条）"):
            st.dataframe(pd.DataFrame([{
                "财报原文科目": m.get("raw_header"), "标准科目编号": m.get("canonical_item"),
                "置信度": m.get("confidence"),
                "来源": "；".join(f"p{s.get('page')}" if s.get("page") is not None
                                else (s.get("locator") or "")
                                for s in (m.get("evidence") or {}).get("sources", [])),
            } for m in mappings]), width='stretch', hide_index=True)

# ---- ⑤ 报告下载 ----
with tab5:
    st.write("三种形式的核查报告（内容一致，用途不同）：")
    items = [("report.pdf", "PDF 正式报告", "application/pdf"),
             ("report.xlsx", "Excel 异常清单",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
             ("report.html", "网页报告", "text/html")]
    found = False
    for name, label, mime in items:
        p = base / name
        if p.exists():
            found = True
            st.download_button(f"⬇ {label}（{p.stat().st_size // 1024} KB）",
                               p.read_bytes(), file_name=f"{run_id}_{name}", mime=mime,
                               key=name)
    if not found:
        st.info("本次运行还没生成报告。执行：\n\n"
                f"```\npython -m fincheck report --run-id {run_id}\n```")

    st.divider()
    st.caption("运行信息")
    st.json({"run_id": run_id,
             "模型": (manifest.get("model") or {}).get("model_id"),
             "提示词哈希": {p["prompt_id"]: p["sha256"][:16] for p in manifest.get("prompts", [])},
             "调用统计": calls})
