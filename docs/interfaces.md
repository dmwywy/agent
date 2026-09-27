# 接口契约 interfaces.md

## 0. 文档用途与生效方式

（1）本文件是全组数据交接的唯一依据。任何字段名、类型、枚举取值、文件路径的变更，都必须先修改本文件、升版本号（v1.0 → v1.1）、并在组内通知，然后才改代码。

（2）本文件生效后，代码不再"按个人理解"读写数据；出现分歧时以本文件为准。

（3）本文件覆盖 5 个接口：[1] 解析侧 → 语义侧、[2] 语义侧 → 规则侧、[3] 规则侧 → 报告侧、[4] 模型输入输出契约、[5] 金融侧 → 全组。

（4）本文件不涉及会计口径的业务判断，业务口径由金融侧（经济创新班成员）确认。

## 1. 全组统一约定

### 1.1 目录结构

```
项目根目录/
├── .env                     不提交；各人凭据
├── .env.example             提交；只有变量名与占位值
├── .gitignore               提交
├── requirements.txt         提交；锁定版本
├── README.md                提交；运行说明
├── docs/
│   ├── interfaces.md        本文件
│   ├── model_plan.md        模型调用方案
│   ├── third_party.md       第三方清单
│   └── evidence/            演练与验证截图
├── prompts/                 提示词文件（含内容哈希记录）
├── rules/
│   ├── rules.yaml           规则配置（程序读取）
│   ├── fields.yaml          字段字典
│   └── tolerance.yaml       容差配置
├── src/                     代码
├── data/
│   ├── demo/                小样例（提交）
│   ├── full/                完整年报（不提交，走共享盘）
│   └── structured/          解析侧输出的结构化数据
└── runs/<run_id>/           每次运行产物（不提交，仅基准缓存例外）
```

### 1.2 运行编号 run_id

（1）格式：`YYYYMMDD_<用途>`，例如 `20260929_demo`、`20260929_schema_check`。

（2）全组协作时共用同一个 `run_id`；各人自测可用自己的用途名。

（3）运行产物落在 `runs/<run_id>/`，互不覆盖。**各人不得写入他人的 run 目录。**

（4）`runs/demo_baseline/` 例外：这是基准运行结果，需提交到仓库，作为"评审无需 API Key 即可复现"的凭据。

### 1.3 编码与格式

| 项 | 规定 |
|---|---|
| 文本编码 | 一律 UTF-8 |
| JSON 写出 | `ensure_ascii=False`；需要比对的文件再加 `sort_keys=True` |
| CSV 交付 | `utf-8-sig`（Excel 双击不乱码） |
| 时间格式 | ISO 8601 带时区，如 `2026-09-29T10:30:00+08:00` |
| 金额 | **字符串形式的十进制数**（如 `"1023500000.00"`），禁止浮点 |
| 比率/百分数 | 用 `"0.123"` 表示 12.3%，不用 `"12.3%"` |
| 空值 | 用 `null`；**禁止用 0 代替"未披露"** |
| 不适用 | 用状态值 `NA`，不用 `null` 表示 |
| 单位 | 由表头决定，写在 `unit_raw`；换算由规则侧负责，解析侧不换算 |

### 1.4 版本号与变更流程

（1）提示词、规则、schema 各自带版本号（`v1`、`v1.1`…）。

（2）任一处变更，必须：改文件 → 升版本号 → 更新本文件 → 通知全组 → 重新跑一次基准并更新 `runs/demo_baseline/`。

（3）**禁止单方面改字段名。** 字段名变了等于所有下游代码失效。

### 1.5 三个"不许混"

（1）**口径不许混**：`scope` 必须区分 `CONSOLIDATED`（合并）与 `PARENT`（母公司）；跨口径比对必须显式声明并降级为需人工复核。

（2）**期间不许混**：`period_type` 必须区分 `FY`（全年累计）、`YTD`（年初至今累计）、`BAL_END`（期末时点）、`BAL_BEG`（期初时点）；时点数与时期数不得互相运算。

（3）**三态不许混**：`null`（未披露）、`NA`（不适用）、`0`（披露为零）是三件不同的事。

## 2. 接口 [1]：解析侧 → 语义侧（数字经济A → 信息工程）

### 2.1 结构化数据文件

（1）路径：`data/structured/<公司简称>_<期间>.json`

（2）内容：一个 JSON 数组，每个元素是一条字段记录。

（3）示例（单条记录）：

```json
{
  "item_code": "REVENUE",
  "item_raw": "营业收入",
  "value": "1023500000.00",
  "unit_raw": "万元",
  "period_type": "FY",
  "period": "2025",
  "scope": "CONSOLIDATED",
  "statement": "INCOME_STATEMENT",
  "page": 42,
  "chunk_id": "p042_t2",
  "raw_text": "营业收入 1,023,500.00",
  "source_hash": "3f9a...c2",
  "parse_status": "OK",
  "confidence": 0.98
}
```

### 2.2 字段表（核心，字段名与类型不得改动）

| 序 | 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| [1] | `item_code` | string | 是 | 标准科目编号，按 `rules/fields.yaml` |
| [2] | `item_raw` | string | 是 | 财报原文科目名 |
| [3] | `value` | **string** | 是 | 十进制字符串，**不换算单位** |
| [4] | `unit_raw` | string | 是 | 表头单位：`元/千元/万元/百万元/亿元` |
| [5] | `period_type` | enum | 是 | `FY / YTD / BAL_END / BAL_BEG` |
| [6] | `period` | string | 是 | 如 `2025`、`2025-12-31` |
| [7] | `scope` | enum | 是 | `CONSOLIDATED / PARENT` |
| [8] | `statement` | string | 是 | `BS / IS / CF / EQ / NOTE` |
| [9] | `page` | int | **是** | 页码；**缺失则该条不得进入核查** |
| [10] | `chunk_id` | string | **是** | 文本片段编号，用于证据反查 |
| [11] | `raw_text` | string | **是** | 原文片段；用于证据校验 |
| [12] | `source_hash` | string | 是 | 来源文件 SHA-256（小写十六进制） |
| [13] | `parse_status` | enum | 是 | `OK / LOW_CONFIDENCE / OCR` |
| [14] | `confidence` | float | 是 | 0～1 |

（1）`page`、`chunk_id`、`raw_text` 三项是**证据三要素**，缺任一项该记录不得进入核查流程。

（2）`parse_status` 为 `LOW_CONFIDENCE` 或 `OCR` 的记录，规则判定不得为 `FAIL`，只能为 `REVIEW`。

（3）找不到的字段**不写进 JSON**，不写 `null`、不写 0。

### 2.3 chunk_id → page 映射表（必需的配套文件）

（1）路径：`data/structured/chunk_index.json`

（2）格式：

```json
{
  "p042_t1": {"page": 42, "source": "2025_annual_report.pdf", "chars": 812},
  "p042_t2": {"page": 42, "source": "2025_annual_report.pdf", "chars": 640}
}
```

（3）用途：模型只被允许引用它见过的 `chunk_id`，**页码由程序用本表反查**，模型不生成页码。这是防止"模型编造页码"的技术手段。

（4）解析侧必须保证：本表中的每个 `chunk_id`，其对应文本可通过 `chunk_id` 取回（供证据校验用 `raw_text` 做包含性判断）。

### 2.4 文本片段交付要求

| 项 | 规定 |
|---|---|
| 单段长度 | ≤ 4000 字符；超出请切分并新建 `chunk_id` |
| 切分边界 | 不切断表格行、不切断数字 |
| 输入内容 | 只给表头行、科目行、正文句子；**不给整章** |
| 数值 | 表头/正文里可以出现数值（那是原文），但**不得附带"这数对不对"的判断** |

### 2.5 拒收条件（出现任一条，语义侧拒收该条记录）

（1）`value` 用浮点数或数字类型而非字符串；
（2）解析侧擅自做了单位换算；
（3）缺少 `page`、`chunk_id` 或 `raw_text`；
（4）`scope` 或 `period_type` 用了枚举外的取值；
（5）同一 `item_code` 同一期间同一口径出现多条且 `value` 冲突，但未标注 `parse_status`。

## 3. 接口 [2]：语义侧 → 规则侧（信息工程 → 数字经济B）

### 3.1 科目归一化结果

（1）路径：`runs/<run_id>/llm/mappings.json`

（2）格式：

```json
{
  "run_id": "20260929_demo",
  "generated_at": "2026-09-29T10:31:00+08:00",
  "prompt_id": "match_item",
  "prompt_version": "v1",
  "model_id": "deepseek-flash",
  "mappings": [
    {"item_raw": "营业总收入", "canonical_item": "TOTAL_REVENUE",
     "confidence": 0.95, "decision": "MODEL"},
    {"item_raw": "其中：主营业务收入", "canonical_item": "",
     "confidence": 0.20, "decision": "MODEL"}
  ],
  "rejected": [
    {"item_raw": "营业收入合计", "reason": "canonical_item 不在标准清单内"}
  ]
}
```

（3）规则侧使用方式：

| `confidence` | 规则侧如何处理 |
|---|---|
| ≥ 0.80 | 直接采用 `canonical_item` 取数 |
| 0.50 ～ 0.80 | 可以采用，但该条规则判定必须降级为 `REVIEW` |
| < 0.50 或 `canonical_item` 为空 | 该字段视为不可用，相关规则记 `NA` |

（4）`decision` 取值：`RULE`（词典命中）/ `MODEL`（模型判定）/ `HUMAN`（人工确认）。**人工确认过的映射必须写回别名表**，避免重复调用模型。

### 3.2 证据校验结果

（1）路径：`runs/<run_id>/llm/evidence_check.json`

（2）格式：

```json
{
  "checked": 12, "passed": 11, "rejected": 1,
  "items": [
    {"task": "match_item", "chunk_id": "p042_t1", "ok": true, "quote": "营业收入"},
    {"task": "unit_period", "chunk_id": null, "ok": false,
     "reason": "chunk_id 不在本次输入中（疑似编造）"}
  ]
}
```

（3）规则：**证据校验不通过的模型结论一律丢弃**，不得进入规则计算，也不得写进报告。

## 4. 接口 [3]：规则侧 → 报告侧（数字经济B → 信息工程）

### 4.1 规则结果文件

（1）路径：`runs/<run_id>/rules/results.json`

（2）格式：

```json
{
  "run_id": "20260929_demo",
  "rules_version": "v1.0",
  "generated_at": "2026-09-29T10:35:00+08:00",
  "results": [
    {
      "rule_id": "BS01@v1.0",
      "dimension": "C内",
      "status": "FAIL",
      "left":  {"item_code": "TOTAL_ASSETS", "value": "5000000000.00"},
      "right": {"expr": "TOTAL_LIAB + TOTAL_EQUITY", "value": "4998000000.00"},
      "diff": "2000000.00",
      "tolerance": "500.00",
      "severity": "HIGH",
      "applicable_if": "always",
      "evidence": {"file": "2025_annual_report.pdf", "page": 48, "chunk_id": "p048_t1",
                   "raw_text": "资产总计 5,000,000,000.00"},
      "human_review": null
    }
  ]
}
```

### 4.2 字段表

| 序 | 字段 | 类型 | 说明 |
|---|---|---|---|
| [1] | `rule_id` | string | `编号@版本`，如 `BS01@v1.0` |
| [2] | `dimension` | enum | 见 4.3 |
| [3] | `status` | enum | 见 4.3 |
| [4] | `left` / `right` | object | 参与比较的两侧；含 `item_code` 与 `value`，或含 `expr` 与 `value` |
| [5] | `diff` | **string** | 左减右，十进制字符串 |
| [6] | `tolerance` | **string** | 本次适用容差 |
| [7] | `severity` | enum | `HIGH / MED / LOW` |
| [8] | `applicable_if` | string | 适用条件描述；不适用时 `status = NA` |
| [9] | `evidence` | object | 含 `file`、`page`、`chunk_id`、`raw_text` |
| [10] | `human_review` | object 或 null | 见 4.4 |

### 4.3 冻结的枚举（不得自创第五个值）

| 枚举 | 允许取值 |
|---|---|
| `status` | `PASS` / `FAIL` / `REVIEW` / `NA` |
| `dimension` | `C内` / `C间` / `C期` / `C附` / `C研` |
| `severity` | `HIGH` / `MED` / `LOW` |

维度含义：`C内` 表内一致性；`C间` 表间一致性；`C期` 跨期间一致性；`C附` 主表与附注一致性；`C研` 研报引用一致性。

### 4.4 人工复核回填区

```json
"human_review": {
  "reviewer": "member_c",
  "reviewed_at": "2026-10-06T20:00:00+08:00",
  "conclusion": "PASS",
  "reason": "属会计政策变更导致的期初重述，公式不适用于本期",
  "evidence_page": 156
}
```

（1）`conclusion` 只允许 `PASS` 或 `FAIL`。

（2）计分时以 `human_review.conclusion` 覆盖原 `status`；报告中**同时呈现"机器初判"与"人工结论"两列**。

（3）未回填时保留 `null`，`status` 维持机器判定。

### 4.5 CSI 结果文件

（1）路径：`runs/<run_id>/rules/csi.json`

（2）格式：

```json
{
  "run_id": "20260929_demo",
  "csi_version": "v1.0",
  "weights_used": {"C内": 0.25, "C间": 0.30, "C期": 0.15, "C附": 0.20, "C研": 0.10},
  "weights_normalized": {"C内": 0.2778, "C间": 0.3333, "C期": 0.1667, "C附": 0.2222, "C研": 0.0},
  "dimensions": {
    "C内": {"score": 0.923, "rules_applicable": 12, "pass": 11, "fail": 1, "review": 0, "na": 0},
    "C研": {"score": null,  "rules_applicable": 0,  "pass": 0,  "fail": 0, "review": 0, "na": 2}
  },
  "csi": 86.8,
  "grade": "B",
  "grade_label": "基本一致",
  "na_dimensions": ["C研"],
  "note": "研报维度无适用规则，该维度权重按比例分摊至其余四维"
}
```

（3）计算规则（两步）：

1. 各维度得分 = (通过数 × 1 + 复核数 × 0.5) ÷ 该维度**可执行规则数**；可执行规则数为 0 时该维度得分记为 `null`。
2. CSI = Σ(维度得分 × 归一化权重) × 100；归一化权重 = 原权重 ÷ Σ(可执行规则数 > 0 的维度原权重)；若全部维度可执行规则数均为 0，则 `csi` 输出 `null`，`grade` 输出 `"NA"`。

（4）等级划分：A（90–100 高度一致）/ B（75–89 基本一致）/ C（60–74 部分一致）/ D（40–59 一致性较弱）/ E（0–39 严重不一致）。

## 5. 接口 [4]：模型输入输出契约（信息工程内部，供 A/B 知悉）

### 5.1 四类输入（`src/inputs.py`）

| 序 | 任务名 | 输入内容 | 关键约束 |
|---|---|---|---|
| [1] | `match_item` | `headers` + `standard_items` | 单批 ≤ 40 条 |
| [2] | `unit_period` | `chunk_id` + `header_text` + `candidates` | 候选表必须给出 |
| [3] | `match_claim` | `claims` + `fields` | **只给科目与期间，不给金额** |
| [4] | `explain_anomaly` | `rule_id` + `diff_desc` + `chunk_id` + `quote` + `candidates` | 候选原因必须给出 |

### 5.2 五类输出（`src/schemas.py`）

`match_item` → `MatchItemOut`；`unit_period` → `UnitPeriodOut`；`match_claim` → `MatchClaimOut`；`explain_anomaly` → `ExplainOut`；`gen_report_text` → `ReportTextOut`。

所有输出必须包含 `evidence`（含 `chunk_id` 与 `quote`）。

### 5.3 模型三条禁则

（1）**不产出数值**：不得输出任何金额数字；数值一律由程序产生。
（2）**不识别表格结构**：行列归属与数值抽取由解析程序完成。
（3）**不生成页码**：只能引用输入中给出的 `chunk_id`。

### 5.4 异常原因候选（冻结）

`UNIT_SCALE`（量级/单位换算错误）/ `PERIOD_MISMATCH`（期间错配）/ `SCOPE_MIXED`（口径混用）/ `VALUE_ERROR`（数值引用错误）/ `SIGN_ERROR`（正负号错误）。

模型只能从上述候选中选择一个；返回候选外的值，该条作废。

## 6. 接口 [5]：金融侧 → 全组（经济创新班成员）

### 6.1 规则表格式

Excel，列固定为：

`规则编号 / 维度 / 核查对象 / 所需字段 / 计算关系 / 容差档位 / 通过标准 / 异常标准 / 人工复核条件 / 数据来源（文件+章节） / 公式验证（实测数值）`

（1）`所需字段` 必须使用 `rules/fields.yaml` 中的 `item_code`。

（2）`容差档位` 填档位名（`MAIN_TABLE` / `NOTE_DETAIL` / `CROSS_YEAR` / `REPORT_REF`），不填具体数值；具体数值见 `rules/tolerance.yaml`。

（3）每条规则必须至少有一条真实数值验算（含差额结果）。

（4）不适用条件通过 `applicable_if` 字段表达；不适用时状态为 `NA`，不计入分母。

### 6.2 模拟研报错误清单格式

CSV，列固定为：

`claim_id / 错误句 / 错误类型 / 标准答案 / 证据应在页码 / 备注`

（1）`错误类型` 从 5.4 的候选中选取。

（2）每类错误至少 2 处，总计 8–10 处。

（3）模拟研报文件首页与页眉必须标注：**"本材料为竞赛模拟样例，非任何机构发布的研究报告"**。

### 6.3 报告审核分工

（1）报告中所有数值、页码、计算公式由程序生成，金融侧不修改。

（2）报告中的专业表述（结论措辞、风险提示、修改建议）由金融侧审核定稿。

（3）报告首页必须包含声明："本系统输出为财务数据一致性核查结果，不构成投资建议，所有结论需人工复核。"

## 7. 配置文件的字段与格式

### 7.1 `rules/fields.yaml`

```yaml
version: "1.0"
fields:
  TOTAL_ASSETS:
    zh: 资产总计
    type: amount
    statement: BS
    period: BAL_END
    source: MAIN
  AR_GROSS:
    zh: 应收账款账面余额
    type: amount
    statement: NOTE
    period: BAL_END
    source: NOTE
    note_ref: "附注七、5"
  CF_NET_INCREASE:
    zh: 现金及现金等价物净增加额
    type: amount
    statement: CF
    period: YTD
    source: MAIN
```

规则：`type` 为 `amount` 的字段值一律为十进制字符串；`source` 为 `NOTE` 的字段必须填 `note_ref`。

### 7.2 `rules/tolerance.yaml`

```yaml
version: "1.0"
profiles:
  MAIN_TABLE:  {abs: "100",  rel: "0",      floor_by_unit: true}
  NOTE_DETAIL: {abs: "1000", rel: "0.0001", floor_by_unit: true}
  CROSS_YEAR:  {abs: "1",    rel: "0",      floor_by_unit: true}
  REPORT_REF:  {abs: "0",    rel: "0.005",  floor_by_unit: false}
```

规则：实际容差 = max(配置绝对值, 相对比例 × max(|左值|,|右值|))；`floor_by_unit: true` 表示绝对值下限不得低于**披露单位的二分之一**（万元披露时即 5000 元）。

### 7.3 `rules/rules.yaml`

```yaml
version: "1.0"
rules:
  - rule_id: BS01@v1.0
    dimension: C内
    object: 合并资产负债表
    expr: "TOTAL_ASSETS - (TOTAL_LIAB + TOTAL_EQUITY)"
    requires: [TOTAL_ASSETS, TOTAL_LIAB, TOTAL_EQUITY]
    scope: CONSOLIDATED
    tolerance: MAIN_TABLE
    severity: HIGH
    applicable_if: "always"
    source: "年报-第十节-合并资产负债表"
```
