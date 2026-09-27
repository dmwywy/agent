# src/llm.py
"""模型调用闸门：全项目唯一出站入口。凭据读取、缓存、重试、JSON 强制、调用记录。"""
import os, json, time, random, hashlib
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

from src.errors import classify, ProviderError
from src.logger import log_event

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

BASE_URL = "https://api.deepseek.com"
MODEL_ID = os.getenv("MODEL_ID", "deepseek-flash").strip()
PARAMS = {"temperature": 0, "top_p": 1}        # ← 不要改这一行，否则缓存全部失效

# ---- 模型白名单：写错在启动瞬间报错，不浪费一次请求 ----
ALLOWED_MODELS = {"deepseek-flash", "deepseek-v4-pro"}
if MODEL_ID not in ALLOWED_MODELS:
    _origin = "环境变量 MODEL_ID" if os.getenv("MODEL_ID") else "src/llm.py 的默认值"
    raise ValueError(
        f"模型名配置错误：来自 {_origin}，当前值 = {MODEL_ID!r}；"
        f"只允许 {sorted(ALLOWED_MODELS)}。"
        f"提示：产品名 DeepSeek-V4.1-Flash 对应的 model 值是 'deepseek-flash'。"
    )


def _client() -> OpenAI:
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("未找到 DEEPSEEK_API_KEY：请检查项目根目录的 .env")
    return OpenAI(api_key=key, base_url=BASE_URL)


def cache_key(prompt_text: str, payload: dict) -> str:
    """缓存指纹：不含凭据、不含时间戳 → 换人换机器命中同一份缓存"""
    h = hashlib.sha256()
    for p in (prompt_text, MODEL_ID, json.dumps(PARAMS, sort_keys=True),
              json.dumps(payload, sort_keys=True, ensure_ascii=False)):
        h.update(p.encode("utf-8"))
    return h.hexdigest()


def call(prompt_text: str, payload: dict, run_id: str = "default",
         user_id: str = "team", task: str = "", offline: bool = False,
         max_retry: int = 3):
    """返回 (结果 dict 或 None, 状态字符串)。任何失败都不中断流程。"""
    ck = cache_key(prompt_text, payload)
    cache = ROOT / "runs" / run_id / "llm_cache" / f"{ck}.json"
    t0 = time.time()

    if cache.exists():                                   # 命中缓存：不联网、不花钱
        data = json.loads(cache.read_text(encoding="utf-8"))
        log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                  status="CACHE_HIT", cache_key=ck[:16],
                  elapsed_ms=int((time.time() - t0) * 1000))
        return data, "CACHE_HIT"

    if offline:                                          # 离线且无缓存
        log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                  status="NO_CACHE_OFFLINE", cache_key=ck[:16])
        return None, "NO_CACHE_OFFLINE"

    client = _client()
    for i in range(max_retry):
        try:
            r = client.chat.completions.create(
                model=MODEL_ID, timeout=90, max_tokens=1500,
                response_format={"type": "json_object"},   # 强制 JSON 输出
                messages=[{"role": "system", "content": prompt_text},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                extra_body={"user_id": user_id},
                **PARAMS)
            data = json.loads(r.choices[0].message.content)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2),
                             encoding="utf-8")
            log_event(run_id, "llm_call", task=task, model=MODEL_ID, status="API_OK",
                      cache_key=ck[:16], attempt=i + 1,
                      elapsed_ms=int((time.time() - t0) * 1000))
            return data, f"API_OK(try={i+1})"

        except json.JSONDecodeError:
            payload = {**payload, "_fix": "上次输出不是合法 json，请只输出 json，不要任何解释"}

        except Exception as e:
            retryable, hint, code = classify(e)
            if not retryable:
                log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                          status="FAILED", http=code, hint=hint)
                if code == 402:                            # 余额不足 → 降级，不中断
                    return None, "QUOTA_EXCEEDED_SWITCH_OFFLINE"
                raise ProviderError(code, hint) from e
            if i == max_retry - 1:
                log_event(run_id, "llm_call", task=task, model=MODEL_ID,
                          status="RETRY_EXHAUSTED", http=code)
                return None, f"RETRY_EXHAUSTED:{code or 'NETWORK'}"
            time.sleep(2 ** i + random.uniform(0, 0.4))

    log_event(run_id, "llm_call", task=task, model=MODEL_ID, status="SCHEMA_INVALID")
    return None, "SCHEMA_INVALID"