# 模型调用方案（信工成员 · 9/27-9/29）

## 1. 模型名称
- 平台：DeepSeek 开放平台；model id：deepseek-flash（默认）/ deepseek-v4-pro
- 位置：src/llm.py 顶部 MODEL_ID，可由 .env 覆盖
- 白名单校验：仅允许上述两个值，写错在启动瞬间报错（不发出请求）
- 版本记录：实际模型标识 + 调用日期写入每次运行的 manifest.json

## 2. API 调用方式
- 协议：OpenAI 兼容；base_url = https://api.deepseek.com
- 参数：temperature=0, top_p=1, max_tokens=1500, response_format={"type":"json_object"}
- 结构化输出：提示词含 "json" 字样并给出输出样例
- 凭据：仅从环境变量/.env 读取；日志与报告一律掩码显示
- 缓存：cache_key = sha256(提示词内容 + 模型标识 + 参数 + 输入数据)，与凭据无关，因此换人换机器命中同一份缓存，评审无需 API Key 即可复现

## 3. 输入文本格式
- 不直接接收 PDF；输入 = 解析器产出的文本片段 + 结构化字段（src/inputs.py）
- 四类输入：科目归一化 / 单位期间识别 / 研报引用匹配 / 异常原因解释
- 硬约束：每段带 chunk_id（页码由程序反查，模型不生成页码）；单段 ≤4000 字符；
  输入中不含待判定的数值结果（模型只做语义匹配，不做数值比较）

## 4. 返回格式
- 5 个 pydantic 模型（src/schemas.py），可导出 JSON Schema
- 三层校验（src/validators.py）：枚举白名单 / 证据真实性 / 报告文字数字白名单
- 校验失败：修复重试 1 次；仍失败则该条标记 REVIEW，不中断流程
- 实证：test_schema_roundtrip.py 输出截图

## 5. 错误处理方式

分流原则：4xx 属"我们的问题，改了再来"（不重试）；429/5xx 与网络异常属"外面的事，等会儿再来"（退避重试）。

| 错误 | 是否重试 | 程序动作 | 触发位置 |
|---|---|---|---|
| 400 格式错误 | 否 | 抛 ProviderError，提示"模型名或参数写错" | src/errors.py NEVER |
| 401 认证失败 | 否 | 抛 ProviderError，提示"Key 无效/带空格/已吊销" | src/errors.py NEVER |
| 402 余额不足 | 否 | 自动降级为离线模式（返回 QUOTA_EXCEEDED_SWITCH_OFFLINE） | src/llm.py |
| 422 参数错误 | 否 | 抛 ProviderError，提示检查参数写法 | src/errors.py NEVER |
| 429 / 500 / 503 | 是 | 指数退避 1s→2s→4s + 抖动，最多 3 次 | src/llm.py |
| 网络超时 / 连接失败 | 是 | 同上，3 次 | src/errors.py |
| 输出非 JSON / 校验失败 | 是（1 次） | 提示词补"只输出 json"后重试一次 | src/llm.py |
| 重试全部失败 | — | 返回 None 与状态串，记录日志，该条标 REVIEW，不中断流程 | src/llm.py |

演练证据（截图见 docs/evidence/）：
- 401_drill.png：认证失败 → 立即停止，不重试
- 400_drill.png：模型名错误 → 平台返回 400，被归类为不可重试错误
- offline_retry.png：断网 → 退避重试耗尽，返回 RETRY_EXHAUSTED:NETWORK，程序不崩

## 6. 不启用联网检索的运行边界

（1）唯一允许出站：api.deepseek.com:443；其余地址由 src/netguard.py 拦截并抛 PermissionError，同时写入 runs/<run_id>/logs.jsonl（allowed=false）。
（2）离线模式（offline=True）不读取凭据、不发起任何网络请求，仅读取本地缓存重放；无缓存时返回 NO_CACHE_OFFLINE。
（3）系统不启用网页搜索、网络爬虫与外部数据库检索；本地检索为本地文件文本匹配，不产生外部请求。
（4）出站记录可机读：每次连接写入 logs.jsonl，含时间、主机、端口、是否放行、用途。
证据：docs/evidence/netguard_block.png（拦截 www.baidu.com:443）
