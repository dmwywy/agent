# 信息工程接口交付（2026-10-07）

## 确认的模型接口

- 产品版本：DeepSeek-V4.1-Flash；请求体 `model` 使用 `deepseek-flash`。
- Endpoint：`POST https://api.deepseek.com/chat/completions`，JSON 请求体。
- 认证：`Authorization: Bearer ${DEEPSEEK_API_KEY}`；密钥仅从 `.env` / 进程环境读取。
- 超时：90 秒。
- 重试：最多 3 次尝试；只对 429、5xx、超时和连接错误指数退避重试。4xx 不重试；非 JSON 内容最多修复重试一次。单条失败进入 REVIEW，不中断整次流程。
- 服务错误：`{"error":{"message":"...","type":"...","param":null,"code":"..."}}`。以 HTTP 状态码分类；不能把 `error.code` 当作 HTTP 状态码。
- JSON：请求 `response_format: {"type":"json_object"}`，system prompt 含 `json` 和样例；响应继续经 Pydantic schema、枚举白名单和证据/数字约束校验。
- 模型参数：`temperature=0`、`top_p=1`、`max_tokens=8000`、`stream=false`，并通过 SDK `extra_body` 传 `thinking: {"type":"disabled"}`。响应解析 `choices[0].message.content` 后按任务 schema 校验。

SDK 调用时 `base_url` 为 `https://api.deepseek.com`，SDK 自动拼出 `/chat/completions`。

## 最小离线契约示例

`offline_mock_input.json` 与 `offline_mock_output.json` 是 synthetic `match_claim` 示例。期间 `2024` 表示时期，模型输出不含 evidence；证据位置由程序从输入解析记录回填。它们是应用层 payload/模型 JSON，不是完整 HTTP envelope。

完整 HTTP 请求体的关键结构：

```json
{
  "model": "deepseek-flash",
  "messages": [
    {"role": "system", "content": "你是研报引用核查助手。请只输出 json 格式。"},
    {"role": "user", "content": "{\"task\":\"match_claim\",\"claims\":[{\"claim_id\":\"demo-001\",\"sentence\":\"示例研究报告片段：营业收入引用需与财报字段核对。\"}],\"fields\":[{\"item_code\":\"REVENUE\",\"period\":\"2024\"}]}"}
  ],
  "temperature": 0,
  "top_p": 1,
  "max_tokens": 8000,
  "stream": false,
  "response_format": {"type": "json_object"},
  "user_id": "member_e",
  "thinking": {"type": "disabled"}
}
```

HTTP 成功响应 envelope 的最小示意：

```json
{
  "id": "chat-mock",
  "object": "chat.completion",
  "model": "deepseek-flash",
  "choices": [{
    "index": 0,
    "finish_reason": "stop",
    "message": {
      "role": "assistant",
      "content": "{\"ok\":true,\"matches\":[{\"claim_id\":\"demo-001\",\"item_code\":\"REVENUE\",\"period\":\"2024\",\"confidence\":0.95,\"reason\":\"句子提到营业收入\"}] }"
    }
  }],
  "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}
}
```

以上 envelope 仅作协议说明；离线缓存保存的是 `message.content` 解析后的对象，不保存 HTTP envelope。

## 对接方式

```powershell
python tools\make_mock.py --task match_item --run-id offline_demo --emit-template
python tools\make_mock.py --task match_claim --run-id offline_demo --emit-template
python -m fincheck run --config configs/demo.yaml --run-id offline_demo --offline
```

离线流程从 `runs/<run_id>/llm_cache/` 读取缓存。当前工具生成的是合法 schema 骨架，语义字段需按实际 payload 编辑后通过 `--response` 注入。

## 需要其他部门答复

这些不是模型接口决议，请项目负责人转交对应成员：

1. **数字经济 A / 数据抽取**：`TOTAL_REVENUE` 与 `REVENUE` 区分、`CASH_END` 与 `CASH_EQ_END` 来源；FY/YTD 与 annual/2026-H1 转换；主表/附注 scope；附注和 MD&A 是否纳入；字段映射表中标“规格缺失”项与仓库字段字典的事实差异。
2. **金融业务 / 规则负责人**：容差 profile 优先级、单位 floor 取整规则，以及 CF01 暂行两项还是完整现金流公式。

按用户指示，信工没有更改 `field_mapping_report.md` 或任何财务规则/容差设置。
