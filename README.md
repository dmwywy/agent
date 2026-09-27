# 财报一致性量化核查智能体（初赛版）

面向上市公司定期报告的财务报告信息一致性量化核查系统。系统读取本地财报与研究报告草稿，
由程序完成数据解析、单位换算、规则校验与指数计算，由大语言模型完成科目语义匹配与
异常原因解释，最终输出结构化核查结果与核查报告。

- 选题方向：数据结构化提取、上市公司财务报告分析、研究报告纠错核查
- 数据环境：半封闭环境，仅访问本地材料与大模型接口，不启用网页搜索与外部数据库
- 交付形态：命令行程序 + 网页界面 + 结构化结果文件（JSON / Excel / 报告）

---

## 一、环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11、macOS、Linux |
| Python | **3.11 或 3.12**（不建议 3.13 及以上，部分库缺少预编译安装包） |
| 网络 | 在线模式需可访问 `api.deepseek.com`；离线模式无需网络 |
| 磁盘 | 建议预留 500 MB（含依赖与样例数据） |

安装依赖：

    python -m pip install -r requirements.txt

Windows 下若使用 PowerShell 激活虚拟环境时报执行策略错误，先执行一次：

    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

---

## 二、初始化（仅首次）

### 1. 创建虚拟环境

Windows（命令提示符）：

    py -3.11 -m venv .venv
    .venv\Scripts\activate.bat

Windows（PowerShell）：

    py -3.11 -m venv .venv
    .\.venv\Scripts\Activate.ps1

macOS / Linux：

    python3.11 -m venv .venv
    source .venv/bin/activate

激活成功的标志：命令行提示符前出现 `(.venv)`。

### 2. 安装依赖

    python -m pip install -r requirements.txt

无外网环境改用离线安装包：

    python -m pip install --no-index --find-links=offline_wheels -r requirements.txt

### 3. 确认解释器

    where python      # macOS / Linux 用 which python
    python -V

第一行路径应指向项目内的 `.venv`，版本应为 3.11.x。

---

## 三、配置 API Key

系统仅在**在线模式**下需要 API Key。Key 由使用者自行申请，本仓库不包含任何密钥。

### 方式一：`.env` 文件（推荐）

1. 复制模板：

       copy .env.example .env        # macOS / Linux 用 cp

2. 到 `platform.deepseek.com` 注册并创建 API Key，填入 `.env`：

       DEEPSEEK_API_KEY=sk-你的key
       MODEL_ID=deepseek-flash

### 方式二：环境变量

Windows（命令提示符，仅当前窗口有效）：

    set DEEPSEEK_API_KEY=sk-你的key

macOS / Linux：

    export DEEPSEEK_API_KEY=sk-你的key

### 方式三：运行时检查

    python check_key.py

该脚本只打印 Key 的掩码与来源，不输出明文。

### 注意事项

1. `.env` 已加入 `.gitignore`，**任何情况下不要提交到版本库**。
2. 密钥仅在调用时从环境变量读取，不写入日志、不写入报告、不写入缓存文件。
3. 日志与报告中一律以掩码形式呈现，形如 `sk-abc***xyz`。
4. 若 `check_key.py` 显示"生效值来自 .env 且与文件一致"但仍返回 401，说明 Key 本身无效，
   请在开放平台重新创建，并确认登录的是你自己的账号。

---

## 四、可用模型

| `MODEL_ID` 取值 | 说明 | 建议用途 |
|---|---|---|
| `deepseek-flash` | 更快、更省 | 科目归一化、单位与期间识别等批量任务（默认） |
| `deepseek-v4-pro` | 能力更强 | 复杂语义判断、异常原因解释 |

代码内置白名单校验：`MODEL_ID` 不在上表范围内时，程序在启动瞬间报错并说明原因，
不会发出任何请求。

---

## 五、运行

所有命令均在**项目根目录**执行。

### 1. 最小调用测试

    python test.py

首次运行应输出 `状态： API_OK(try=1)`；再次运行应输出 `状态： CACHE_HIT`。
第二次命中缓存时不发起网络请求，也不产生费用。

### 2. 契约自检

    python testcontract.py

检查六项：模型名称、调用方式、输入格式、返回格式、错误处理、联网边界。

### 3. 字段字典自检

    python tools\check_fields.py

校验 `rules/fields.yaml` 自身完整性，以及 `rules/rules.yaml` 中引用的字段是否都已定义。

### 4. 返回结构与证据校验

    python test_schema_roundtrip.py

用真实模型输出验证 schema 校验路径与证据真实性校验。

### 5. 密钥配置诊断

    python check_key.py

### 6. 待接入的命令（开发中）

    python -m fincheck run --config configs/demo.yaml              # 在线运行
    python -m fincheck run --config configs/demo.yaml --offline    # 离线复现

---

## 六、离线复现（评审推荐，无需 API Key）

系统采用"调用缓存 + 结果比对"的方式支持无凭据复现。

### 原理

缓存键的构成为：

    cache_key = sha256(提示词内容 + 模型标识 + 推理参数 + 输入数据)

其中**不包含 API Key、不包含时间戳、不包含机器名**。因此同一份输入在不同机器、不同使用者
手中命中同一份缓存，输出结果一致。

### 操作步骤

1. 打开 `runs/demo_baseline/`，即为预先跑好的基准运行结果。
2. 确认 `llm_cache/` 目录中存在缓存文件（数量大于零）。
3. 移除网络连接（或直接断网）。
4. 运行：

       python test.py
       python test_schema_roundtrip.py

5. 预期结果：输出内容与 `runs/demo_baseline/` 中记录的结果一致，且全程未联网、未使用密钥。

### 离线运行的判定

| 现象 | 含义 |
|---|---|
| 状态显示 `CACHE_HIT` | 命中缓存，未发起请求 |
| 状态显示 `NO_CACHE_OFFLINE` | 离线模式下无对应缓存，该条转为需人工复核 |
| 状态显示 `API_OK` | 发起了真实请求，说明缓存未命中 |

---

## 七、目录结构

    .
    ├── .env                  凭据文件，不提交
    ├── .env.example          变量模板，提交
    ├── .gitignore
    ├── requirements.txt
    ├── README.md
    ├── check_key.py          密钥配置诊断
    ├── test.py               最小调用测试
    ├── testcontract.py       契约自检
    ├── test_schema_roundtrip.py  返回结构与证据校验
    ├── docs/
    │   ├── model_plan.md     模型调用方案
    │   ├── interfaces.md     接口契约
    │   └── evidence/         验证与演练截图
    ├── prompts/
    │   └── match_item.txt    科目归一化提示词
    ├── rules/
    │   ├── fields.yaml       字段字典
    │   ├── rules.yaml        规则配置
    │   └── tolerance.yaml    容差配置
    ├── src/
    │   ├── llm.py            模型调用闸门（缓存、重试、JSON 强制输出）
    │   ├── errors.py         错误分类与提示
    │   ├── inputs.py         四类输入构造
    │   ├── schemas.py        五类返回结构契约
    │   ├── validators.py     三层校验
    │   ├── netguard.py       联网白名单与离线自检
    │   ├── logger.py         事件记录
    │   └── manifest.py       运行清单
    ├── tools/
    │   └── check_fields.py   字段字典一致性自检
    ├── data/
    │   ├── demo/             样例材料
    │   └── structured/       解析输出的结构化数据
    └── runs/
        ├── demo_baseline/    基准运行结果（提交，用于离线复现）
        └── <run_id>/         各次运行产物（不提交）

---

## 八、输出产物

每次运行在 `runs/<run_id>/` 下生成：

| 文件 | 内容 |
|---|---|
| `manifest.json` | 运行清单：模型标识、推理参数、提示词哈希、输入文件哈希、依赖版本、调用统计 |
| `logs.jsonl` | 事件记录：每次出站连接、每次模型调用及其状态与耗时 |
| `llm_cache/*.json` | 模型原始返回内容，用于离线重放 |
| `rules/results.json` | 规则核查结果明细 |
| `rules/csi.json` | 各维度得分与综合一致性指数 |
| `report.*` | 核查报告（PDF、Excel、网页三种形式） |

运行清单中的 `calls` 字段分别统计 `api_ok`、`cache_hit`、`failed`，用于核对实际调用次数。

---

## 九、联网边界

1. 运行期唯一允许的出站目标为 `api.deepseek.com:443`；其余地址由 `src/netguard.py`
   拦截并记录到 `logs.jsonl`。
2. 离线模式不读取凭据、不发起网络请求，仅读取本地缓存。
3. 系统不启用网页搜索、网络爬虫与外部数据库检索。
4. 系统中出现的"本地检索"指本地文件的文本匹配，不产生外部请求。

---

## 十、错误处理

| 错误 | 是否重试 | 程序行为 |
|---|---|---|
| 400 请求体格式错误 | 否 | 停止并提示检查模型名与参数 |
| 401 认证失败 | 否 | 停止并提示检查密钥 |
| 402 余额不足 | 否 | 自动降级为离线模式 |
| 422 参数错误 | 否 | 停止并提示检查参数写法 |
| 429 请求速率上限 | 是 | 指数退避重试，最多 3 次 |
| 500 / 503 服务端异常 | 是 | 指数退避重试，最多 3 次 |
| 网络超时或连接失败 | 是 | 指数退避重试，最多 3 次 |
| 输出不是合法 JSON | 是 | 提示词补充说明后重试 1 次 |
| 重试全部失败 | — | 该条记为需人工复核，不中断整体流程 |

---

## 十一、协作约定

1. 主分支只放可运行的版本。
2. 各人运行产物放入 `runs/<用户名>_<日期>_<用途>/`，互不覆盖。
3. `runs/demo_baseline/` 为公共基准结果，**修改后需全组同步**。
4. 字段名、枚举取值、容差档位的变更须先改 `docs/interfaces.md` 并升版本号，再改代码。
5. 大数据文件（完整年报、视频）走共享盘，不进入版本库。
6. 提交前执行 `git status`，确认清单中不含 `.env`、`.venv/`、`runs/<其他 run_id>/`。

---

## 十二、第三方依赖与说明

| 名称 | 版本 | 来源 | 许可证 | 用途 |
|---|---|---|---|---|
| Python | 3.11 | python.org | PSF | 运行环境 |
| openai | 见 requirements.txt | PyPI | Apache-2.0 | 调用兼容接口的客户端 |
| pydantic | 见 requirements.txt | PyPI | MIT | 返回结构定义与校验 |
| python-dotenv | 见 requirements.txt | PyPI | BSD-3-Clause | 读取环境变量文件 |
| PyYAML | 见 requirements.txt | PyPI | MIT | 读取规则与字段配置 |
| DeepSeek API | 见 openai 平台文档 | 深度求索开放平台 | 商业服务 | 语义匹配与文字生成 |

使用范围：上述依赖仅用于文档解析、结构校验、配置读取与模型调用。
第三方模型与商业软件不提交源代码，其名称、版本、调用方式与使用范围见本节与 `docs/model_plan.md`。

---

## 十三、免责声明

本系统输出为财务数据一致性核查结果，用于辅助人工复核，**不构成投资建议、评级或买卖意见**。
所有核查结论均需由专业人员复核。系统中使用的模拟研究材料仅为竞赛演示样例，
不代表任何机构发布的观点。年报等原始材料仅用于本地受控环境下的展示与核查，不对外分发。
