---
name: gen_commentary
description: 基于程序已算好的确定数值（经营指标、派生计算、CSI、异常清单、研报比对）撰写研报式经营评价。模型只组织语言，不做任何运算。
---

# Skill：gen_commentary（研报式评价）

## 定位

报告末尾的"大模型经营评价（仅供参考）"。与"只给结构、不给数字"的结论文字相反，
本 Skill **给数字**——但数字全部来自程序产物（提取值 + `src/derived.py` 派生计算），
模型不得自行推算任何新数。

## 代码落点

| 环节 | 文件 |
|---|---|
| 入参构造 | `src/inputs.py::build_gen_commentary` |
| 数字白名单抽取 | `src/inputs.py::collect_allowed_numbers` |
| 派生计算 | `src/derived.py::compute_derived` |
| 确定性财务分析 | `src/financial_analysis.py::analyze` |
| 输出 schema | `src/schemas.py::ReportTextOut` |
| 提示词 | `prompts/gen_commentary.txt` |
| 编排 | `src/pipeline.py::run_commentary` |
| 报告渲染 | `src/report.py::_commentary_sections` |

## 输入

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | string | 固定 `"gen_commentary"` |
| `metrics` | object[] | 核心经营指标 `[{code,name,period,value}]`，≤ 24 条 |
| `derived` | object[] | 程序派生指标（同比/比率/亿元换算/情景测算），≤ 40 条 |
| `analysis` | object[] | 确定性财务分析（盈利能力/费用率/趋势/结构四组），≤ 40 条；与 `derived` 同 code 的条目已去重 |
| `csi` | object | `{csi, grade, grade_label}` |
| `anomalies` | object[] | 异常清单（含确定 diff / tolerance），≤ 20 条 |
| `research` | object[] | 研报比对不一致项（研报值/财报值/差异），≤ 20 条 |
| `counts` | object | 各清单条数（metrics/derived/analysis/anomalies/research），模型可引用这些程序已知计数 |

## 输出（经 `ReportTextOut` 校验）

```json
{"ok": true, "text": "…四模块（【收入分析】【盈利能力】【现金流】【风险提示】）的研报式评价…"}
```

## 校验层

1. Pydantic 结构；2. **数字白名单**：`text` 中每个数字都必须落在
   `collect_allowed_numbers(payload)` 集合内（`src/validators.check_no_amounts`），
   模型编造任何未提供的数值都会被拦截。
