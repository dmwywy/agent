# test_tasks.py   运行：python -m pytest -q
from src.errors import classify
from src.schemas import SCHEMA_BY_TASK
from src.inputs import (build_match_item, build_unit_period,
                        build_match_claim, build_explain_anomaly)


def test_model_whitelist():
    import src.llm as L
    assert L.MODEL_ID in {"deepseek-flash", "deepseek-v4-pro"}


def test_temperature_fixed():
    import src.llm as L
    assert L.PARAMS["temperature"] == 0


def test_schema_count_and_export():
    assert len(SCHEMA_BY_TASK) == 5
    for cls in SCHEMA_BY_TASK.values():
        cls.model_json_schema()


def test_inputs_constructors():
    p = build_match_item(["营业收入"], [{"code": "REVENUE", "name": "营业收入"}])
    assert p["task"] == "match_item"
    for f in (build_unit_period, build_match_claim, build_explain_anomaly):
        assert callable(f)


def test_error_classify():
    E = type("E", (Exception,), {"status_code": 401})
    retry, hint, code = classify(E())
    assert retry is False and code == 401


def test_prompt_files_naming():
    """五个提示词必须都能按约定文件名加载"""
    from src.tasks import load_prompt
    for task in SCHEMA_BY_TASK:
        text, meta = load_prompt(task)
        assert "json" in text.lower(), f"{task} 提示词缺少 json 字样"
        assert len(meta["sha256"]) == 64


def test_netguard_installed():
    from src.netguard import install_allowlist, ALLOW_HOST
    install_allowlist(run_id="pytest")
    assert ALLOW_HOST == "api.deepseek.com"