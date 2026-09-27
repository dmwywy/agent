# test.py
import json
from pathlib import Path
from src.llm import call, MODEL_ID, PARAMS, BASE_URL
from src.manifest import new_manifest, save_manifest, sha256_text

run_id = "20260927_test"
PROMPT_PATH = Path("prompts/match_item.txt")
PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

payload = {
    "headers": ["营业总收入", "营业收入", "其中：主营业务收入"],
    "standard_items": [
        {"code": "REVENUE", "name": "营业收入"},
        {"code": "TOTAL_REVENUE", "name": "营业总收入"},
    ],
}

# [1] 先建清单
m = new_manifest(
    run_id=run_id,
    operator="member_e",
    model_cfg={"provider": "deepseek", "base_url": BASE_URL,
               "model_id": MODEL_ID, "params": PARAMS},
    prompts=[{"prompt_id": "match_item", "version": "v1",
              "sha256": sha256_text(PROMPT)}],
    input_files=[PROMPT_PATH],          # 相对路径也能用了
)

# [2] 再调用
try:
    data, status = call(PROMPT, payload, run_id=run_id, user_id="member_e")
except Exception as e:
    print("调用失败：", e)
    m["status"] = "FAILED"
    save_manifest(run_id, m)            # 失败也要落盘，便于追溯
    raise SystemExit(1)

print("状态：", status)
print(json.dumps(data, ensure_ascii=False, indent=2))

# [3] 统计并补全
if status.startswith("API_OK"):
    m["calls"]["api_ok"] += 1
elif status == "CACHE_HIT":
    m["calls"]["cache_hit"] += 1
else:
    m["calls"]["failed"] += 1
m["calls"]["total"] += 1
m["status"] = "OK" if data is not None else "PARTIAL"

save_manifest(run_id, m)
print("manifest 已写入：runs/%s/manifest.json" % run_id)