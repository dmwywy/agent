# test_tasks.py   运行：python -m pytest -q
import json

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


def test_post_checks_enum_whitelist():
    """模型返回越界的 canonical_item 应被三层校验拦下（无需联网）。"""
    from src.tasks import _post_checks
    bad = {"items": [{"raw_header": "某科目", "canonical_item": "NOT_A_CODE",
                      "confidence": 0.9}]}
    errs = _post_checks(bad, "match_item", None)
    assert any("越界" in e for e in errs)


def test_post_checks_evidence():
    """模型引用一个它没见过的 chunk_id，应判证据无效。"""
    from src.tasks import _post_checks
    bad = {"evidence": {"chunk_id": "p999_t1", "quote": "营业收入"}}
    errs = _post_checks(bad, "unit_period", {"chunks": {"p042_t1": "营业收入"}})
    assert any("chunk_id" in e for e in errs)


def test_post_checks_empty_item_code_ok():
    """模型判"无法判断"时 item_code 为空字符串，不应被判越界。"""
    from src.tasks import _post_checks
    ok = {"matches": [{"claim_id": "c01", "item_code": "", "period": "",
                       "confidence": 0.2}]}
    assert _post_checks(ok, "match_claim", None) == []


def test_program_evidence_tasks_skip_evidence_check():
    """证据由程序回填的任务，不应因缺 evidence 被判失败。"""
    from src.tasks import PROGRAM_EVIDENCE_TASKS, _post_checks
    assert {"match_item", "match_claim"} <= PROGRAM_EVIDENCE_TASKS
    for task in PROGRAM_EVIDENCE_TASKS:
        assert _post_checks({}, task, {"chunks": {"p001_t1": "x"}}) == []


def test_claim_fields_carries_no_amounts():
    """interfaces.md §5.1：给模型的字标清单只含科目与期间，不得带金额。"""
    from src.pipeline import _claim_fields
    recs = [{"item_code": "REVENUE", "period": "2024", "value": "170899152276.34"},
            {"item_code": "REVENUE", "period": "2024", "value": "170899152276.34"},
            {"item_code": "NET_PROFIT", "period": "2024", "value": "89334728025.90"}]
    fields = _claim_fields(recs)
    assert len(fields) == 2                      # 同 (科目,期间) 去重
    for f in fields:
        assert set(f) == {"item_code", "period"}
        assert "value" not in f


def test_claim_evidence_flags_unknown_claim_id():
    """模型返回不存在的 claim_id → 证据无效，不得进入核查。"""
    from src.pipeline import _attach_claim_evidence
    got = _attach_claim_evidence(
        [{"claim_id": "c01"}, {"claim_id": "不存在"}],
        {"c01": {"source": "rpt.docx", "section": "核心观点", "sentence": "…"}})
    assert got[0]["evidence"]["verified"] is True
    assert got[1]["evidence"]["verified"] is False


def test_multi_indicator_matches_are_kept():
    """一条 claim 可对应多个指标（同一 claim_id 多条 match），不得去重丢失。"""
    from src.pipeline import _attach_claim_evidence
    src = {"c01": {"source": "r.docx", "section": "核心观点", "sentence": "…"}}
    got = _attach_claim_evidence(
        [{"claim_id": "c01", "item_code": "TOTAL_REVENUE"},
         {"claim_id": "c01", "item_code": "NP_PARENT"}], src)
    assert len(got) == 2
    assert {g["item_code"] for g in got} == {"TOTAL_REVENUE", "NP_PARENT"}
    assert all(g["evidence"]["verified"] for g in got)


# ---------- A：第五个任务的输入构造器 ----------

def test_gen_report_text_excludes_amounts():
    """报告结论输入只含结构与科目编号，不得把金额/差额喂给模型。"""
    from src.inputs import build_gen_report_text
    p = build_gen_report_text(
        {"C内": {"pass": 10, "fail": 1, "review": 0, "na": 0}},
        [{"rule_id": "BS01", "dimension": "C内", "item_code": "TOTAL_ASSETS",
          "status": "FAIL", "left": "5000000000.00", "diff": "2000000.00",
          "tolerance": "500.00"}],
        "基本一致")
    assert p["task"] == "gen_report_text"
    assert set(p["anomaly_items"][0]) == {"rule_id", "dimension", "item_code", "status"}
    assert "5000000000" not in json.dumps(p, ensure_ascii=False)


def test_all_five_tasks_have_input_builders():
    """5 个任务必须各有输入构造器（此前 gen_report_text 缺失）。"""
    from src import inputs
    from src.schemas import SCHEMA_BY_TASK
    for task in SCHEMA_BY_TASK:
        assert hasattr(inputs, f"build_{task}"), f"缺 build_{task}"


# ---------- C：缓存与失败分流 ----------

def test_cache_key_stable_and_sensitive():
    """同输入同键；提示词或输入数据变一个字节即不同键。"""
    from src.llm import cache_key
    k = cache_key("prompt", {"a": 1})
    assert cache_key("prompt", {"a": 1}) == k
    assert cache_key("prompt ", {"a": 1}) != k
    assert cache_key("prompt", {"a": 2}) != k


def test_cache_key_covers_all_inference_params(monkeypatch):
    """推理参数必须参与缓存键，否则改了参数却命中旧结果（model_plan §2.5）。"""
    import src.llm as L
    base = L.cache_key("p", {"a": 1})
    monkeypatch.setattr(L, "THINKING", {"type": "enabled"})
    assert L.cache_key("p", {"a": 1}) != base, "thinking 未参与缓存键"
    monkeypatch.setattr(L, "THINKING", {"type": "disabled"})
    monkeypatch.setattr(L, "PARAMS", {**L.PARAMS, "max_tokens": 123})
    assert L.cache_key("p", {"a": 1}) != base, "max_tokens 未参与缓存键"


def test_offline_cache_hit_without_network():
    """离线模式命中缓存应返回 CACHE_HIT，且不读取凭据、不联网。"""
    import src.llm as L
    run_id, prompt, payload = "pytest_cache_hit", "只输出 json 的提示词", {"task": "x", "n": 1}
    key = L.cache_key(prompt, payload)
    cache = L.ROOT / "runs" / run_id / "llm_cache" / f"{key}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"ok": True}, ensure_ascii=False), encoding="utf-8")
    data, status = L.call(prompt, payload, run_id=run_id, offline=True)
    assert status == "CACHE_HIT"
    assert data == {"ok": True}


def test_empty_content_is_not_retried(monkeypatch):
    """推理烧光额度导致正文为空时只调用一次——重试同一输入只会白烧额度。"""
    import src.llm as L
    from types import SimpleNamespace
    calls = {"n": 0}

    def fake_create(**kw):
        calls["n"] += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="length",
                                     message=SimpleNamespace(content=""))],
            usage=SimpleNamespace(completion_tokens=8000))

    fake_client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(L, "_client", lambda: fake_client)

    data, status = L.call("提示词 json", {"task": "x", "n": 2}, run_id="pytest_empty")
    assert data is None and status == "OUTPUT_EMPTY"
    assert calls["n"] == 1, "空正文应直接返回，不得重试"


def test_offline_miss_does_not_split_exponentially(monkeypatch):
    """缓存未命中时不得触发指数级拆分——拆得再小也不会变好。"""
    import src.pipeline as P
    calls = {"n": 0}

    def fake_call_task(*a, **k):
        calls["n"] += 1
        return None, "NO_CACHE_OFFLINE"

    monkeypatch.setattr(P, "call_task", fake_call_task)
    claims = [{"claim_id": f"c{i}", "sentence": "x"} for i in range(20)]
    matches, status = P._match_claim_batched(claims, [], "pytest_run", True)
    assert matches == []
    assert calls["n"] == 1, f"应只调用一次，实际 {calls['n']} 次"

# ---------- D：研报引用数值比对（C研 维度）----------

_ZH = {"TOTAL_REVENUE": "营业总收入", "REVENUE": "营业收入", "NP_PARENT": "归属母公司股东的净利润",
       "REVENUE_MOUTAI": "茅台酒营业收入", "REVENUE_SERIES": "系列酒营业收入"}


def test_figure_binds_to_nearest_preceding_name():
    """金额归属它前面最近的指标名，而不是"任意前置名字之后第一个金额"。"""
    from src.research_check import associate_figures
    s = "2024年公司实现营业总收入 1741.4 亿元/同比+15.66%，归母净利润 862.3 亿元/同比+15.38%。"
    got = associate_figures(s, _ZH)
    assert got["TOTAL_REVENUE"]["value"] == 174140000000
    assert got["NP_PARENT"]["value"] == 86230000000


def test_segment_revenue_not_attributed_to_total():
    """分部营收不得挂到总营收上——否则会用分部数去比总营收，必然误报。"""
    from src.research_check import associate_figures
    got = associate_figures("茅台酒 2024 年营收 1495.3 亿元/+15.28%", _ZH)
    assert "REVENUE" not in got, "『茅台酒…营收』的金额被误挂到营业收入"
    assert got["REVENUE_MOUTAI"]["value"] == 149530000000


def test_non_amount_units_are_ignored():
    """吨/股等非金额单位不得被当成钱。"""
    from src.research_check import associate_figures
    got = associate_figures("茅台酒销量 4.6 万吨/+10.22%", _ZH)
    assert got.get("REVENUE_MOUTAI") is None


def test_malformed_unit_is_flagged():
    """"亿万元"这类畸形单位必须判为单位错误，不能因为数值凑巧接近就放行。"""
    from src.research_check import associate_figures
    got = associate_figures("归母净利润 862.3 亿万元/同比+15.38%", _ZH)
    assert got["NP_PARENT"]["bad_unit"] == "亿万元"


def test_research_check_flags_unit_error_against_real_value():
    """用真实财报值验证：862.3亿万元的单位错误必须判 FAIL/UNIT_SCALE。"""
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "NP_PARENT", "period": "2024",
                "evidence": {"verified": True}}]
    claims = {"c1": {"sentence": "归母净利润 862.3 亿万元/同比+15.38%"}}
    records = [{"item_code": "NP_PARENT", "period": "2024", "period_type": "FY",
                "value": "86228146421.62", "page": 64, "chunk_id": "p064_t1"}]
    out = check(matches, claims, records)
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["status"] == "FAIL" and r["cause"] == "UNIT_SCALE"
    assert r["dimension"] == "C研"


def test_research_check_quarter_vs_annual_is_review_not_fail():
    """单季数与年度累计不可比：判 REVIEW，不得判 FAIL（误报控制）。"""
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2024",
                "evidence": {}}]
    claims = {"c1": {"sentence": "其中 2024Q4 实现营业总收入 510.2 亿元/同比+12.77%"}}
    records = [{"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
                "value": "174144069958.25", "page": 63, "chunk_id": "p063_t1"}]
    out = check(matches, claims, records)
    assert out["results"] == []
    assert out["review"][0]["status"] == "REVIEW"


def test_research_check_passes_within_tolerance():
    """年报引用误差在 0.5% 内应判 PASS。"""
    from src.research_check import check
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2024",
                "evidence": {}}]
    claims = {"c1": {"sentence": "2024年公司实现营业总收入 1741.4 亿元"}}
    records = [{"item_code": "TOTAL_REVENUE", "period": "2024", "period_type": "FY",
                "value": "174144069958.25", "page": 63, "chunk_id": "p063_t1"}]
    out = check(matches, claims, records)
    assert out["results"][0]["status"] == "PASS"


# ---------- E：文件夹入口 ----------

def test_year_from_name():
    """报告期从文件名识别；识别不到才用 --year 兜底。"""
    from pathlib import Path
    from src.ingest import year_from_name
    assert year_from_name(Path("2024年年度报告.docx")) == 2024
    assert year_from_name(Path("贵州茅台【2025全年】模拟研究报告.docx")) == 2025
    assert year_from_name(Path("无年份.docx"), fallback=2023) == 2023


def test_classify_by_content_not_filename(monkeypatch):
    """按文件内容分类，不看文件名——各人写法不一，靠名字会误判。"""
    from pathlib import Path
    from src import ingest
    monkeypatch.setattr(ingest, "read_text",
                        lambda p: "合并资产负债表 合并利润表 合并现金流量表")
    assert ingest.classify(Path("随便叫什么.docx"))[0] == "annual"
    monkeypatch.setattr(ingest, "read_text",
                        lambda p: "核心观点 投资评级 盈利预测 风险提示")
    assert ingest.classify(Path("随便叫什么.docx"))[0] == "research"
    monkeypatch.setattr(ingest, "read_text", lambda p: "一段无关的话")
    assert ingest.classify(Path("随便叫什么.docx"))[0] == "unknown"


def test_load_structured_ignores_non_field_records():
    """目录里混入研报 claims（也是 JSON 数组）时不得被当成财报字段记录。"""
    from src.pipeline import ROOT, load_structured
    d = ROOT / "runs" / "pytest_struct"
    d.mkdir(parents=True, exist_ok=True)
    (d / "chunk_index.json").write_text('{"p001_t1": {"page": 1}}', encoding="utf-8")
    (d / "fields.json").write_text(json.dumps(
        [{"item_code": "REVENUE", "period": "2024", "value": "100.00"}],
        ensure_ascii=False), encoding="utf-8")
    (d / "claims.json").write_text(json.dumps(
        [{"claim_id": "c1", "sentence": "营业收入 100 亿元"}], ensure_ascii=False),
        encoding="utf-8")
    got = load_structured("runs/pytest_struct")
    assert len(got) == 1 and got[0]["item_code"] == "REVENUE"


def test_claims_carry_source_hash():
    """研报 claim 必须带内容哈希：文件名没变但内容被改时要能发现。"""
    from src.ingest import ROOT
    p = ROOT / "data" / "demo" / "研报claims_2024.json"
    if not p.exists():
        return                      # 基线缺失时跳过，不误报
    claims = json.loads(p.read_text(encoding="utf-8"))
    assert claims and all(len(c.get("source_hash", "")) == 64 for c in claims)
