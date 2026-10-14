# MCP 接口设计（规划中）

> **状态：规划中，当前以 CLI / Web 入口替代。**
> 本文件给出若将本项目按 MCP（Model Context Protocol）封装时的接口设计，
> 输入输出沿用 [`docs/interfaces.md`](./interfaces.md) 的 JSON 契约，**不引入任何新契约**。

## 1. 设计原则

- **零侵入**：MCP 层只做"薄封装"，不修改 `src/` 中的业务流程代码。
  现有 CLI（`python -m fincheck …`）与 Web（`streamlit run app.py`）已是完整入口，
  MCP 只是把它们按工具（tool）暴露给 MCP 客户端。
- **可复现**：MCP 工具的输出与 CLI 产物逐字节一致（都落在 `runs/<run-id>/`）。

## 2. 工具清单

| MCP tool | 对应现有入口 | 关键入参 | 产物 |
|---|---|---|---|
| `fincheck_ingest` | `python -m fincheck ingest --from <目录> --run-id <id> [--offline]` | `from_dir`, `run_id`, `offline`, `report_period` | `runs/<id>/ingested/*.json` |
| `fincheck_run` | `python -m fincheck run --config configs/demo.yaml --run-id <id> [--offline]` | `config`, `run_id`, `offline` | `runs/<id>/rules/*.json`、`llm/*.json`、`manifest.json` |
| `fincheck_report` | `python -m fincheck report --run-id <id>` | `run_id`, `formats` | `runs/<id>/report.{pdf,xlsx,html}` |
| `calculate_ratios` | `src/financial_analysis.py::analyze` | `records` | 盈利能力/费用率/趋势/结构四组指标 JSON（纯计算，不落盘） |
| `check_consistency` | `src/rules_engine.py::run_rules` | `records`, `run_id` | 规则结果 + CSI 评分 JSON |

> `calculate_ratios` / `check_consistency` 是本项目可暴露的两个**纯确定性工具**，
> 与 Skill `financial-ratio-calculator` / `consistency-checker` 一一对应。

## 3. 资源（resources）

| MCP resource URI | 内容 |
|---|---|
| `fincheck://runs` | 历史运行编号列表 |
| `fincheck://runs/{run_id}/manifest.json` | 运行清单（提示词哈希、调用统计） |
| `fincheck://runs/{run_id}/rules/csi.json` | 一致性指数与维度明细 |

## 4. 与 Skill 的关系

`skills/` 下共 **8** 个 `SKILL.md`：
- **模型侧任务**（4）：`match_item` / `match_claim` / `explain_anomaly` / `gen_commentary`，
  与 `src.schemas.SCHEMA_BY_TASK` 一一对应；
- **确定性能力**（4）：`financial-ratio-calculator`（财务比率计算）/
  `consistency-checker`（一致性核查，封装 27 条规则）/ `field-extractor`（字段提取）/
  `report-generator`（报告生成），对应 MCP 工具或 CLI/Web 入口。

MCP 工具声明的是**流程侧入口**，Skill 声明的是**能力边界**；二者通过
`runs/<run-id>/` 的落盘产物衔接，互不依赖。

## 5. 未实现说明

当前仓库**不含** MCP server/client 代码。**无 MCP 运行时不代表能力缺失：当前以 CLI /
Web 接口替代 MCP**——`python -m fincheck …`（CLI）与 `streamlit run app.py`（Web）已是
完整入口，`calculate_ratios`、`check_consistency` 等工具能力也已在 `src/` 内实现并可直接调用。
若决赛现场要求运行 MCP，可在不修改业务代码的前提下，按上表将现有函数/子命令包一层
MCP tool 即可，输出与 CLI 产物逐字节一致。
