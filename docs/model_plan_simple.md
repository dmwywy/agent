# 模型调用方案（信工成员，9/27 至 9/30）

## 1. 模型名称
- 平台：DeepSeek 开放平台；接口协议：OpenAI 兼容
- model 标识：deepseek-flash（默认，对应 V4.1-Flash）/ deepseek-v4-pro（可选）
- 配置位置：src/llm.py 顶部 MODEL_ID，可由 .env 覆盖
- 白名单校验：仅允许上述两个取值，写错在模块加载时即报错，不发出任何请求
- 版本记录：实际使用的模型标识与调用日期写入 runs/<run_id>/manifest.json

## 2. API 调用方式
- base_url：https://api.deepseek.com
- 推理参数：temperature=0、top_p=1、max_tokens=1500、stream=false
- 结构化输出：response_format={"type":"json_object"}；提示词必须含 "json" 字样并给出输出样例
- 凭据来源：仅从环境变量或 .env 读取；日志、报告、manifest 中一律掩码显示
- 缓存机制：cache_key = sha256(提示词内容 + 模型标识 + 推理参数 + 输入数据)，不含凭据与时间戳
  因此同一输入在任何机器上命中同一份缓存，评审无需 API Key 即可复现
- 运行清单：每次运行生成 manifest.json，记录模型标识、参数、提示词内容哈希、输入文件哈希、
  代码版本、依赖版本与调用统计（api_ok / cache_hit / failed）

## 3. 输入文本格式
- 不直接接收 PDF；输入为本地解析器产出的文本片段与结构化字段（src/inputs.py）
- 四类输入：科目归一化、单位与期间识别、研报引用匹配、异常原因解释
- 硬性约束：
  (1) 每个文本片段携带唯一 chunk_id，其与页码的对应关系保存在本地映射表中，模型不生成页码；
  (2) 单段文本不超过 4000 字符，超出则切分；
  (3) 输入中不包含需要判定的数值结果，模型只做语义匹配，数值比较由本地程序完成。

## 4. 返回格式
- 5 个返回结构（src/schemas.py），可导出 JSON Schema；键名与 prompts/<task>.txt 一一对应
- 三层校验（src/validators.py）：
  (1) 枚举白名单：canonical_item / item_code / cause 只能取约定集合内的值；
  (2) 证据校验：chunk_id 必须来自本次输入，quote 必须真实存在于该片段文本中；
  (3) 数字白名单：报告文字中出现的任何数字都必须能在程序给定结果中找到同值，否则拒绝。
- 失败处理：schema 校验失败自动携带错误信息修复重试一次；仍失败则记 REVIEW，不中断整个流程。

## 5. 错误处理方式
| 错误 | 是否重试 | 程序动作 | 提示 |
| --- | --- | --- | --- |
| 400 格式错误 | 否 | 停止并抛 ProviderError | 请求体格式错误（多为模型名或参数拼写错误） |
| 401 认证失败 | 否 | 停止并抛 ProviderError | 认证失败（Key 无效、带了空格，或已被吊销） |
| 402 余额不足 | 否 | 自动降级为离线模式 | 余额不足，改用缓存重放 |
| 422 参数错误 | 否 | 停止并抛 ProviderError | 参数错误，检查 model / max_tokens |
| 429 速率上限 | 是 | 1s/2s/4s 退避加抖动，并发降为 1 | 日志 WARN |
| 500 / 503 | 是 | 同退避策略，最多 3 次 | 日志 WARN |
| 网络超时、连接失败 | 是 | 同上 3 次 | 日志 WARN |
| 输出非 JSON 或结构校验失败 | 是 | 携带错误信息修复重试 1 次 | 日志 WARN |
| 全部重试失败 | 否 | 该条标记 REVIEW，继续执行 | 报告列入需人工复核 |

证据：三张演练截图见 docs/evidence/401_drill.png、400_whitelist.png、offline_retry.png。

## 6. 不启用联网检索的运行边界
- 唯一允许出站：api.deepseek.com:443；其余地址由 src/netguard.py 拦截并记入 logs.jsonl
- 离线模式（--offline）不读取凭据、不发起任何网络请求，仅使用本地缓存重放
- 系统不启用网页搜索、网络爬虫与外部数据库检索；本地检索为本地文件文本匹配，无外部请求
- 启动自检：offline_selftest() 主动尝试连接 API 域名，若可达即报错（说明有模块绕过缓存）