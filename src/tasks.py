# src/tasks.py
"""任务级接口：加载提示词 -> 调用闸门 -> schema 校验 -> 失败修复重试。
上层（pipeline / cli）只使用本模块，不直接接触 src.llm.call。"""
from pathlib import Path
from src.llm import call
from src.schemas import SCHEMA_BY_TASK
from src.manifest import sha256_text

ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = ROOT / "prompts"
DEFAULT_VERSION = "v1"


def load_prompt(task: str, version: str = DEFAULT_VERSION) -> tuple[str, dict]:
    """读取提示词原文，并返回其内容哈希（供 manifest 记录）。
    兼容两种命名：<task>.txt 与 <task>（无扩展名）。"""
    p = PROMPT_DIR / f"{task}.txt"
    if not p.exists():
        alt = PROMPT_DIR / task
        if alt.exists():
            p = alt
        else:
            raise FileNotFoundError(
                f"提示词文件不存在：{p}（也未找到无扩展名的 {alt}）。"
                f"约定文件名为 prompts/<task>.txt，可用任务：{list(SCHEMA_BY_TASK)}"
            )
    text = p.read_text(encoding="utf-8")
    meta = {"prompt_id": task, "version": version, "sha256": sha256_text(text),
            "file": p.name}
    return text, meta


def call_task(task: str, payload: dict, run_id: str, user_id: str = "team",
              offline: bool = False) -> tuple[dict | None, str]:
    """返回 (通过 schema 校验的结果 dict 或 None, 状态字符串)"""
    if task not in SCHEMA_BY_TASK:
        raise KeyError(f"未注册的任务：{task}（可用：{list(SCHEMA_BY_TASK)}）")
    prompt_text, _meta = load_prompt(task)
    schema_cls = SCHEMA_BY_TASK[task]

    data, status = call(prompt_text, payload, run_id=run_id,
                        user_id=user_id, offline=offline)
    if data is None:
        return None, status

    try:
        return schema_cls.model_validate(data).model_dump(), status
    except Exception as e:
        # 修复重试一次：把校验错误回传，要求只按样例输出
        fix_payload = dict(payload)
        fix_payload["_fix"] = (f"上次输出未通过结构校验：{e}。"
                               "请严格按 json 样例输出，只包含约定字段。")
        data2, status2 = call(prompt_text, fix_payload, run_id=run_id,
                              user_id=user_id, offline=offline)
        if data2 is None:
            return None, status2 + "|SCHEMA_FIX_NO_RESULT"
        try:
            return schema_cls.model_validate(data2).model_dump(), status2 + "|SCHEMA_FIXED"
        except Exception as e2:
            return None, f"SCHEMA_INVALID:{e2}"