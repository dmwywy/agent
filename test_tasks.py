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
    assert len(SCHEMA_BY_TASK) == 6
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
    """白名单只拦对外地址，且**本机回环必须放行**——否则网页界面直接不可用。"""
    from src.netguard import (ALLOW_HOST, install_allowlist, is_allowed,
                              uninstall_allowlist)
    assert ALLOW_HOST == "api.deepseek.com"
    assert is_allowed("api.deepseek.com", 443)
    assert is_allowed("127.0.0.1", 8899)          # Streamlit 本地服务
    assert is_allowed("localhost", 80)
    assert not is_allowed("evil.example.com", 443)
    assert not is_allowed("api.deepseek.com", 80)  # 端口也要对

    install_allowlist(run_id="pytest")
    try:
        import socket
        socket.socket().connect(("127.0.0.1", 9))   # 回环放行（连不上是另一回事）
    except PermissionError:
        raise AssertionError("netguard 拦截了本机回环，会搞垮网页界面")
    except OSError:
        pass                                        # 连接被拒/超时都正常
    finally:
        uninstall_allowlist()                       # 必须还原，否则污染后续测试


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


def test_gen_commentary_filters_to_core_metrics():
    """研报式评价只喂核心科目，非核心科目（如附注明细）不进入输入。"""
    from src.inputs import build_gen_commentary
    p = build_gen_commentary(
        [{"code": "TOTAL_REVENUE", "name": "营业总收入", "period": "2024",
          "value": "174140000000.00"},
         {"code": "SUPP_DEPRECIATION", "name": "补充资料固定资产折旧", "period": "2024",
          "value": "123.45"}],
        {"csi": 87.5, "grade": "A", "grade_label": "基本一致"},
        [], [], "基本一致")
    codes = {m["code"] for m in p["metrics"]}
    assert "TOTAL_REVENUE" in codes
    assert "SUPP_DEPRECIATION" not in codes


def test_commentary_whitelist_rejects_fabricated_number():
    """模型评价里出现程序没给过的数字，必须被数字白名单校验拦下。"""
    from src.inputs import build_gen_commentary, collect_allowed_numbers
    from src.validators import check_no_amounts
    p = build_gen_commentary(
        [{"code": "TOTAL_REVENUE", "name": "营业总收入", "period": "2024",
          "value": "174140000000.00"}],
        {"csi": 87.5, "grade": "A", "grade_label": "基本一致"}, [], [], "基本一致")
    allowed = collect_allowed_numbers(p)
    # 引用了输入里没有的"毛利率 92.5%"这种编造数字 → 必须被拦
    errs = check_no_amounts("公司毛利率为 92.5%，超出预期。", allowed)
    assert any("未经程序确认" in e for e in errs)
    # 合法引用输入里的数字不被拦
    assert check_no_amounts("营业总收入 174140000000.00 元。", allowed) == []


def test_all_five_tasks_have_input_builders():
    """每个任务必须各有输入构造器。"""
    from src import inputs
    from src.schemas import SCHEMA_BY_TASK
    for task in SCHEMA_BY_TASK:
        assert hasattr(inputs, f"build_{task}"), f"缺 build_{task}"


# ---------- B：派生计算（程序算数） ----------

_DERIVED_RECS = [
    {"item_code": "REVENUE", "period": "2024", "value": "1000000000"},
    {"item_code": "REVENUE", "period": "2023", "value": "800000000"},
    {"item_code": "OPERATING_COST", "period": "2024", "value": "400000000"},
    {"item_code": "OPERATING_COST", "period": "2023", "value": "300000000"},
    {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "1000000000"},
    {"item_code": "TOTAL_REVENUE", "period": "2023", "value": "800000000"},
]


def test_derived_yoy_ratio_and_scale():
    """同比、比率、亿元换算必须是程序确定性算出的确切值（对齐研报的对比口径）。"""
    from src.derived import compute_derived
    got = {it["code"]: it for it in compute_derived(_DERIVED_RECS)}
    assert got["SCALE_TOTAL_REVENUE"]["value"] == "10.0"      # 10 亿元
    assert got["SCALE_TOTAL_REVENUE"]["unit"] == "亿元"
    assert got["YOY_TOTAL_REVENUE"]["value"] == "25.00"       # (1000-800)/800
    assert got["RATIO_GROSS"]["value"] == "60.0"              # (10-4)/10
    assert got["PCTCHG_RATIO_GROSS"]["unit"] == "pcts"


def test_derived_skips_missing_inputs():
    """数据不足的派生项必须缺席，而不是猜一个数出来。"""
    from src.derived import compute_derived
    got = {it["code"] for it in compute_derived(
        [{"item_code": "TOTAL_REVENUE", "period": "2024", "value": "1000000000"}])}
    assert "SCALE_TOTAL_REVENUE" in got        # 本期够，能换算亿元
    assert "YOY_TOTAL_REVENUE" not in got      # 没有上期，不得给同比
    assert "RATIO_GROSS" not in got            # 没有营业成本，不得给毛利率


def test_derived_basis_and_name_contain_no_digits():
    """口径说明/名称/单位不得含阿拉伯数字——否则公式里的常数会污染数字白名单。"""
    import re
    from src.derived import compute_derived
    for it in compute_derived(_DERIVED_RECS):
        for field in ("name", "unit", "basis"):
            assert not re.search(r"\d", it[field]), f"{it['code']} 的 {field} 混入了数字"


def test_commentary_payload_carries_derived_into_whitelist():
    """派生值随 payload 进入数字白名单：模型可引用同比/毛利率，但仍不能编造新数。"""
    from src.derived import compute_derived
    from src.inputs import build_gen_commentary, collect_allowed_numbers
    from src.validators import check_no_amounts
    derived = compute_derived(_DERIVED_RECS)
    p = build_gen_commentary(
        [{"code": "TOTAL_REVENUE", "name": "营业总收入", "period": "2024",
          "value": "1000000000"}],
        {"csi": 95, "grade": "A", "grade_label": "高度一致"}, [], [], "高度一致", derived)
    assert p["counts"]["derived"] == len(p["derived"]) == len(derived)
    allowed = collect_allowed_numbers(p)
    # 引用程序派生出的同比与毛利率 → 放行
    assert check_no_amounts("营业总收入 10.0 亿元，同比 25.00%，毛利率 60.0%。", allowed) == []
    # 程序给负数时，正文写成"同比下降12.50%"（负号在词后、不在数字里）同样放行
    assert check_no_amounts("归母净利润同比下降 12.50%。", {"-12.50"}) == []
    assert check_no_amounts("同比 -12.50%", {"-12.50"}) == []
    # 但程序只给了正数时，把符号写反必须被拦下
    assert any("未经程序确认" in e
               for e in check_no_amounts("同比 -25.00%", allowed))
    # 程序没给过的数字 → 仍必须被拦
    assert any("未经程序确认" in e
               for e in check_no_amounts("毛利率高达 88.8%。", allowed))


def test_commentary_sections_split():
    """模型输出的「【小节名】正文」要能拆成标题+正文，供三种报告分子标题渲染。"""
    from src.report import _commentary_sections
    secs = _commentary_sections("【核心观点】经营稳健。\n【经营情况】营业总收入…\n续写一句。")
    assert [s[0] for s in secs] == ["核心观点", "经营情况"]
    assert secs[0][1] == "经营稳健。"
    assert secs[1][1] == "营业总收入…续写一句。"


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
    monkeypatch.setattr(L, "_client", lambda *_a, **_k: fake_client)

    data, status = L.call("提示词 json", {"task": "x", "n": 2}, run_id="pytest_empty")
    assert data is None and status == "OUTPUT_EMPTY"
    assert calls["n"] == 1, "空正文应直接返回，不得重试"


def test_api_key_override_reaches_client(monkeypatch, tmp_path):
    """网页表单里临时填入的 Key 必须一路透传到出站客户端，且不进缓存键。"""
    import src.llm as L
    from types import SimpleNamespace
    seen = []
    # 隔离缓存目录：否则第一次调用会把缓存写进 runs/，重复运行变成 CACHE_HIT、断言不再成立
    monkeypatch.setattr(L, "ROOT", tmp_path)

    def fake_client(api_key=None):
        seen.append(api_key)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop",
                                         message=SimpleNamespace(content='{"ok": true}'))],
                usage=SimpleNamespace(completion_tokens=1)))))

    monkeypatch.setattr(L, "_client", fake_client)
    data, status = L.call("提示词 json", {"task": "k"}, run_id="pytest_key",
                          api_key="sk-ui-supplied")

    assert data == {"ok": True} and status.startswith("API_OK")
    assert seen and seen[0] == "sk-ui-supplied", "表单填的 Key 应传给出站客户端"
    # 缓存键不含凭据：同一输入恒得同一指纹（换 Key 不会让缓存失效）
    assert L.cache_key("提示词 json", {"task": "k"}) == L.cache_key("提示词 json", {"task": "k"})


def test_call_task_retry_keeps_api_key(monkeypatch):
    """校验失败后的修复重试也必须带上网页填的临时 Key——否则重试会静默回落 .env。"""
    import src.tasks as T
    seen = []

    def fake_call(prompt, payload, **kw):
        seen.append(kw.get("api_key"))
        if len(seen) == 1:                      # 第一次返回越界枚举，触发修复重试
            return {"items": [{"raw_header": "某科目",
                               "canonical_item": "NOT_A_CODE", "confidence": 0.9}]}, "API_OK(try=1)"
        return {"items": []}, "API_OK(try=1)"

    monkeypatch.setattr(T, "call", fake_call)
    out, status = T.call_task("match_item",
                              {"headers": ["某科目"], "standard_items": []},
                              "pytest_retry_key", api_key="sk-ui-supplied")

    assert len(seen) == 2, "越界枚举应触发一次修复重试"
    assert all(s == "sk-ui-supplied" for s in seen), "重试调用也不得丢掉表单填的 Key"
    assert out == {"ok": True, "items": [], "reason": ""} and "SCHEMA_FIXED" in status


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


def test_malformed_unit_does_not_crash():
    """畸形单位（亿万元/千万元）必须判单位错误，且不得因量级字查表抛 KeyError。"""
    from src.research_check import associate_figures, check
    zh = {"TOTAL_REVENUE": "营业总收入", "NP_PARENT": "归属母公司股东的净利润"}
    for unit in ("亿万元", "千万元"):
        got = associate_figures(f"归母净利润 862.3 {unit}", zh)
        assert got["NP_PARENT"]["bad_unit"] == unit, unit

    # 端到端：不得抛异常，且判 FAIL/UNIT_SCALE
    matches = [{"claim_id": "c1", "item_code": "TOTAL_REVENUE", "period": "2025",
                "evidence": {}}]
    out = check(matches, {"c1": {"sentence": "营业总收入 1721 千万元"}},
                [{"item_code": "TOTAL_REVENUE", "period": "2025", "period_type": "FY",
                  "value": "172054171890.91", "page": 61, "chunk_id": "p061_t1"}])
    assert out["results"][0]["status"] == "FAIL"
    assert out["results"][0]["cause"] == "UNIT_SCALE"


def test_research_check_survives_bad_sentence(monkeypatch):
    """单条解析异常只丢该条转人工复核，不得打断整体流程。"""
    from src import research_check as RC
    monkeypatch.setattr(RC, "associate_figures",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    out = RC.check(
        [{"claim_id": "c1", "item_code": "REVENUE", "period": "2024", "evidence": {}}],
        {"c1": {"sentence": "营业收入 100 亿元"}},
        [{"item_code": "REVENUE", "period": "2024", "period_type": "FY",
          "value": "10000000000.00", "page": 63, "chunk_id": "p063_t1"}])
    assert out["results"] == []
    assert out["review"] and "解析异常" in out["review"][0]["reason"]


# ---------- F：规则引擎（只凭财报就出结果）----------

def test_safe_eval_only_arithmetic():
    """规则表达式求值必须拒绝任意代码，只放行 Decimal 四则运算。"""
    from decimal import Decimal
    from src.rules_engine import ExpressionError, safe_eval
    env = {"A": Decimal("10"), "B": Decimal("4")}
    assert safe_eval("A - (B + 1)", env) == Decimal("5")
    assert safe_eval("-A", env) == Decimal("-10")
    for bad in ("__import__('os')", "A.__class__", "A if A else B"):
        try:
            safe_eval(bad, env)
            raise AssertionError(f"应当拒绝：{bad}")
        except (ExpressionError, SyntaxError):
            pass


def test_rules_engine_balance_sheet_identity():
    """资产=负债+权益 成立时判 PASS，人为改动后判 FAIL。"""
    from src.rules_engine import run_rules
    base = [
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "资产总计 1000.00"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "400.00", "unit_raw": "元", "raw_text": "负债合计 400.00"},
        {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "600.00", "unit_raw": "元", "raw_text": "所有者权益合计 600.00"},
    ]
    out = run_rules(base, "pytest_rules_ok")
    bs01 = [r for r in out["results"] if r["rule_id"].startswith("BS01")]
    assert bs01 and bs01[0]["status"] == "PASS"

    broken = [dict(r) for r in base]
    broken[0]["value"] = "1234.00"          # 人为破坏平衡
    out2 = run_rules(broken, "pytest_rules_bad")
    bs01b = [r for r in out2["results"] if r["rule_id"].startswith("BS01")]
    assert bs01b[0]["status"] == "FAIL"
    assert bs01b[0]["diff"] == "234.00"


def test_rules_engine_missing_fields_are_na_not_fail():
    """字段缺失判 NA（不计入分母），不得判 FAIL——否则是误报。"""
    from src.rules_engine import run_rules
    out = run_rules([
        {"item_code": "CASH_END", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "100.00", "unit_raw": "元", "raw_text": "货币资金 100.00"},
    ], "pytest_rules_na")
    nb = [r for r in out["results"] if r["rule_id"].startswith("NB01")]
    assert nb and all(r["status"] == "NA" for r in nb)


def test_cross_period_rule_detects_mismatch():
    """同一时点的期末数 vs 下一年报的期初数不等时应判 FAIL。"""
    from src.rules_engine import run_rules
    out = run_rules([
        {"item_code": "CASH_END", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "100.00", "unit_raw": "元", "raw_text": "货币资金 100.00"},
        {"item_code": "CASH_END", "period": "2024-12-31", "period_type": "BAL_BEG",
         "value": "999.00", "unit_raw": "元", "raw_text": "货币资金 999.00"},
    ], "pytest_cross")
    xq = [r for r in out["results"] if r["rule_id"].startswith("XQ_CASH")]
    assert xq and xq[0]["status"] == "FAIL"


def test_zero_anomaly_run_does_not_crash():
    """一条 FAIL/REVIEW 都没有时不落 explanations.json，流程不得因此崩掉。

    此前 pipeline 无条件读该文件，导致"数据干净、零异常"的运行在最后一步报
    FileNotFoundError——越干净的输入越走不通。
    """
    import json as _json
    from src.pipeline import ROOT, explain_summary
    run_id = "pytest_zero_anomaly"
    base = ROOT / "runs" / run_id / "rules"
    base.mkdir(parents=True, exist_ok=True)
    (base / "explanations.json").unlink(missing_ok=True)
    assert explain_summary(run_id) == "无异常，无需解释"

    (base / "explanations.json").write_text(
        _json.dumps({"explained": 3}, ensure_ascii=False), encoding="utf-8")
    assert explain_summary(run_id) == "3 条"


def test_csi_normalizes_and_grades():
    """CSI 只对有可执行规则的维度归一化；全不可执行时输出 null 而非 0。"""
    from src.rules_engine import compute_csi
    cfg = {"version": "1.0", "dimensions": {"C内": {"weight": 0.25}, "C研": {"weight": 0.10}}}
    ok = compute_csi([{"dimension": "C内", "status": "PASS"}], cfg)
    assert ok["csi"] == 100.0 and ok["grade"] == "A"
    assert ok["weights_normalized"]["C内"] == 1.0        # 只剩一个维度 → 权重归一到 1
    empty = compute_csi([], cfg)
    assert empty["csi"] is None and empty["grade"] == "NA"


def test_cross_period_rule_accepts_distinct_end_and_beg_items():
    """期末/期初可以是两个不同编号（权益变动表把两者拆开），也必须能对上。"""
    from src.rules_engine import run_rules
    out = run_rules([
        {"item_code": "SI_UNDIST_END", "period": "2023-12-31", "period_type": "BAL_END",
         "value": "500.00", "unit_raw": "元", "raw_text": "四、本期期末余额 …"},
        {"item_code": "SI_UNDIST_BEG", "period": "2023-12-31", "period_type": "BAL_BEG",
         "value": "500.00", "unit_raw": "元", "raw_text": "二、本年期初余额 …"},
    ], "pytest_cross_beg")
    r = [x for x in out["results"] if x["rule_id"].startswith("XQ_UNDIST")]
    assert r and r[0]["status"] == "PASS", "期初/期末拆成两个编号后跨期规则失效"


def test_report_year_rule_aligns_end_beg_and_period_amounts():
    """报告年度对齐：期末(2024-12-31)、期初(2023-12-31)、时期数(2024) 合到同一期再算。

    这三种期间按 (period, period_type) 分组会落进三个组、每组都缺字段而误判 NA。
    """
    from src.rules_engine import run_rules
    recs = [
        {"item_code": "SI_UNDIST_END", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "四、本期期末余额 …"},
        {"item_code": "SI_UNDIST_BEG", "period": "2023-12-31", "period_type": "BAL_BEG",
         "value": "800.00", "unit_raw": "元", "raw_text": "二、本年期初余额 …"},
        {"item_code": "SI_NP_PARENT", "period": "2024", "period_type": "FY",
         "value": "300.00", "unit_raw": "元", "raw_text": "（一）综合收益总额 …"},
        {"item_code": "SI_SURPLUS_APPROP", "period": "2024", "period_type": "FY",
         "value": "-50.00", "unit_raw": "元", "raw_text": "1．提取盈余公积 …"},
        {"item_code": "SI_DIVIDEND", "period": "2024", "period_type": "FY",
         "value": "-50.00", "unit_raw": "元", "raw_text": "3．对所有者（或股东）的分配 …"},
    ]
    out = run_rules(recs, "pytest_report_year")
    si = [r for r in out["results"] if r["rule_id"].startswith("SI01")]
    assert si and si[0]["status"] == "PASS", "报告年度对齐未生效"
    assert si[0]["period"] == "2024"

    broken = [dict(r) for r in recs]
    broken[0]["value"] = "1234.00"
    out2 = run_rules(broken, "pytest_report_year_bad")
    si2 = [r for r in out2["results"] if r["rule_id"].startswith("SI01")]
    assert si2[0]["status"] == "FAIL" and si2[0]["diff"] == "234.00"


def test_zero_if_absent_only_defaults_the_listed_fields():
    """固定模板表的空白行按 0 计（zero_if_absent），锚点字段缺失仍判 NA。

    调节项若被一律按 0 计（取到值也不用），等式会退化成"差额=全部调节项之和"。
    """
    from src.rules_engine import run_rules
    recs = [
        {"item_code": "SUPP_CF_OPERATING", "period": "2024", "period_type": "FY",
         "value": "1300.00", "unit_raw": "元", "raw_text": "经营活动产生的现金流量净额 …"},
        {"item_code": "SUPP_NET_PROFIT", "period": "2024", "period_type": "FY",
         "value": "1000.00", "unit_raw": "元", "raw_text": "净利润 …"},
        # 有取到值的调节项必须用真实值（此处 300 = 1300 − 1000）
        {"item_code": "SUPP_DEPRECIATION", "period": "2024", "period_type": "FY",
         "value": "300.00", "unit_raw": "元", "raw_text": "固定资产折旧 …"},
    ]
    out = run_rules(recs, "pytest_zero_if_absent")
    cf = [r for r in out["results"] if r["rule_id"].startswith("CF02")]
    assert cf and cf[0]["status"] == "PASS", f"调节项被当成 0：{cf[0].get('fields')}"
    assert cf[0]["fields"]["SUPP_DEPRECIATION"] == "300.00"

    # 锚点字段缺失 → NA，不得因为"其余项都能按 0 计"而算成通过
    out2 = run_rules([r for r in recs if r["item_code"] != "SUPP_NET_PROFIT"],
                     "pytest_zero_if_absent_na")
    cf2 = [r for r in out2["results"] if r["rule_id"].startswith("CF02")]
    assert cf2 and all(r["status"] == "NA" for r in cf2)


def test_rule_library_size_and_field_references():
    """规则库 20–30 条（《项目思路》要求），且每条引用的字段都在字典里。"""
    import yaml
    from src.pipeline import ROOT
    fields = yaml.safe_load((ROOT / "rules" / "fields.yaml").read_text(encoding="utf-8"))
    cfg = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    codes = set(fields["fields"])
    rules = cfg["rules"]
    assert 20 <= len(rules) <= 30, f"规则条数 {len(rules)} 不在 20–30"
    for rule in rules:
        for key in ("requires", "zero_if_absent"):
            for c in rule.get(key, []):
                assert c in codes, f"{rule['rule_id']} 引用未定义字段 {c}"
        for key in ("item", "item_beg"):
            if rule.get(key):
                assert rule[key] in codes
    # 五个维度都要有规则，否则 CSI 会缺维度
    dims = {r["dimension"] for r in rules}
    assert {"C内", "C间", "C期", "C附"} <= dims


# ---------- F2：分部数据 / 所有者权益变动表 / 附注明细的抽取 ----------

def test_segment_table_attribution_by_section():
    """分部表按小节归属：同名行在不同小节含义不同，不切小节就会串数。"""
    from tools.extract_fields import _scan_segment_table
    rows = [
        ["主营业务分行业情况"],
        ["分行业", "营业收入", "营业成本", "毛利率（%）"],
        ["酒类", "1000.00", "400.00", "60.00"],
        ["主营业务分产品情况"],
        ["分产品", "营业收入", "营业成本", "毛利率（%）"],
        ["茅台酒", "700.00", "250.00", "64.00"],
        ["其他系列酒", "300.00", "150.00", "50.00"],
    ]
    parsed = _scan_segment_table(rows)
    got = {(r[0], r[1]) for r in parsed}
    assert ("REVENUE_LIQUOR", "1000.00") in got
    assert ("REVENUE_MOUTAI", "700.00") in got
    assert ("REVENUE_SERIES", "300.00") in got
    assert ("COST_SERIES", "150.00") in got
    # item_raw 是原文行名，且必须出现在 raw_text 里（否则证据校验判未通过）
    for code, value, raw_text, _no, label in parsed:
        assert label in raw_text and label in ("酒类", "茅台酒", "其他系列酒")
    # 列头没确认"营业收入在第 1 列"时整表不取，不能按猜的列号取数
    bad = [["主营业务分产品情况"], ["分产品", "营业成本", "营业收入"], ["茅台酒", "250.00", "700.00"]]
    assert _scan_segment_table(bad) == []


def test_equity_table_parsing_carries_year_to_continuation():
    """权益变动表在 Word 里常被拆成"表主体 + 续表"，续表没有年份，必须沿用上一张的。"""
    from tools.extract_fields import _scan_equity_table
    head = ["项目", "2024 年度"]
    title_row = ["", "归属于母公司所有者权益", "少数股东权益", "所有者权益合计"]
    cells = [""] * 16
    cells[0], cells[9], cells[11] = "四、本期期末余额", "50.00", "900.00"
    cells[13], cells[14], cells[15] = "950.00", "30.00", "980.00"
    main = [head, title_row, cells]
    cont = [["6．其他"] + [""] * 15, cells]

    rows, year = _scan_equity_table(main, None)
    assert year == 2024
    got = {r[0]: r for r in rows}
    assert got["SI_UNDIST_END"][1] == "900.00" and got["SI_UNDIST_END"][2] == "2024-12-31"
    assert got["SI_EQ_TOTAL_END"][1] == "980.00"
    # item_raw 必须是原文行名，且真的出现在 raw_text 里（证据包含性校验要用）
    assert got["SI_UNDIST_END"][6] == "四、本期期末余额"
    assert got["SI_UNDIST_END"][6] in got["SI_UNDIST_END"][4]

    # 续表：没有年份表头 → 沿用 2024；列数不符 → 整表不取（宁可不取也不取错）
    rows2, year2 = _scan_equity_table(cont, year)
    assert year2 == 2024 and {r[0] for r in rows2} == set(got)
    rows3, _ = _scan_equity_table([head, ["四、本期期末余额"] + [""] * 5], None)
    assert rows3 == []


def test_equity_row_period_types_are_not_mixed():
    """期末/期初是时点数，综合收益与利润分配是时期数——期间不许混（§1.5）。"""
    from tools.extract_fields import _scan_equity_table
    head = ["项目", "2024 年度"]

    def row(label, undist="10.00"):
        c = [""] * 16
        c[0], c[11] = label, undist
        return c

    rows, _ = _scan_equity_table(
        [head, row("二、本年期初余额"), row("四、本期期末余额"),
         row("（一）综合收益总额"), row("1．提取盈余公积"), row("3．对所有者（或股东）的分配")], None)
    by_code = {r[0]: (r[2], r[3]) for r in rows}
    assert by_code["SI_UNDIST_BEG"] == ("2023-12-31", "BAL_BEG")
    assert by_code["SI_UNDIST_END"] == ("2024-12-31", "BAL_END")
    assert by_code["SI_NP_PARENT"] == ("2024", "FY")
    assert by_code["SI_SURPLUS_APPROP"] == ("2024", "FY")
    assert by_code["SI_DIVIDEND"] == ("2024", "FY")


def test_cf_supplement_matcher_is_order_sensitive():
    """补充资料表用「包含」匹配且顺序敏感：短键排前面会把长键的行抢走。"""
    from tools.extract_fields import NOTE_TARGETS, _match_note_item
    spec = NOTE_TARGETS["现金流量表补充资料"]
    assert _match_note_item(spec, "净利润") == "SUPP_NET_PROFIT"
    assert _match_note_item(spec, "经营活动产生的现金流量净额") == "SUPP_CF_OPERATING"
    assert _match_note_item(spec, "现金及现金等价物净增加额") == "SUPP_CF_NET_INCREASE"
    # 2024 写"折耗"、2025 写"折旧"，同一行两种写法都要认
    assert _match_note_item(spec, "固定资产折旧、油气资产折耗、生产性生物资产折旧") == "SUPP_DEPRECIATION"
    assert _match_note_item(spec, "固定资产折旧、油气资产折旧、生产性生物资产折旧") == "SUPP_DEPRECIATION"
    assert _match_note_item(spec, "处置固定资产、无形资产和其他长期资产的损失（收益以“ － ”号填列）") == "SUPP_DISPOSAL_LOSS"
    assert _match_note_item(spec, "固定资产报废损失（收益以“ - ”号填列）") == "SUPP_SCRAP_LOSS"
    # 小节标题行也含"净利润"，但它以冒号结尾、不是数据行，必须挡掉
    assert _match_note_item(spec, "1.将净利润调节为经营活动现金流量：") is None
    assert _match_note_item(spec, "2.不涉及现金收支的重大投资和筹资活动：") is None


def test_note_headings_distinguish_section_and_subsection():
    """一级小节 "5、 应收账款" 与子小节 "（2）按坏账计提方法分类披露" 必须分开认。

    混为一谈的话，note 会被子小节标题覆盖，货币资金小节之后的表全被错抽。
    """
    from tools.extract_fields import NOTE_HEADING_RE, NOTE_SUBHEADING_RE
    assert NOTE_HEADING_RE.match("5、 应收账款").group(1) == "应收账款"
    assert NOTE_HEADING_RE.match("8. 货币资金√适用").group(1) == "货币资金"
    assert NOTE_HEADING_RE.match("（1）现金流量表补充资料") is None
    assert NOTE_SUBHEADING_RE.match("（1）按账龄披露").group(1) == "按账龄披露"
    assert NOTE_SUBHEADING_RE.match("(2).按坏账计提方法分类披露").group(1) == "按坏账计提方法分类披露"


def test_ar_note_row_target_sums_to_net_receivable():
    """应收账款附注：净额 = 账面余额 − 坏账准备（规则表 7/26 条的口径）。"""
    from tools.extract_fields import _match_row_target, _squash
    got = _match_row_target("应收账款", "按坏账计提方法分类披露")
    assert got is not None, "应收账款坏账计提附注未配置"
    assert _match_row_target("其他应收款", "按坏账计提方法分类披露") is None, \
        "其他应收款附注结构相同，不能当成应收账款"
    assert _match_row_target("应收账款", "按账龄披露") is None
    row = ["合计", "19,995,290.75", "/", "1,021,098.00", "/", "18,974,192.75"]
    _, cur, _ = got
    from decimal import Decimal
    assert Decimal(row[cur["AR_GROSS"]].replace(",", "")) \
        - Decimal(row[cur["AR_BADDEBT"]].replace(",", "")) == Decimal("18974192.75")
    assert _squash("（一）综合收益总额") == "(一)综合收益总额"


# ---------- G：核查报告生成 ----------

def _make_run_fixture(run_id: str) -> None:
    """造一个自包含的运行产物目录。

    测试不得依赖仓库里既有的 runs/<某次运行>——那是本地产物，队友 clone 后不存在，
    断言会因为"没数据"而失败（而不是因为代码错）。这里显式造数据。
    """
    from src.pipeline import ROOT
    base = ROOT / "runs" / run_id
    (base / "rules").mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(json.dumps(
        {"calls": {"api_ok": 1, "cache_hit": 0, "cache_miss": 0, "failed": 0, "total": 1},
         "model": {"model_id": "deepseek-flash"},
         "prompts": [{"prompt_id": "match_item", "sha256": "a" * 64}]},
        ensure_ascii=False), encoding="utf-8")
    (base / "rules" / "results.json").write_text(json.dumps({"results": [
        {"rule_id": "BS01@1.0", "dimension": "C内", "status": "FAIL", "period": "2024-12-31",
         "expr": "TOTAL_ASSETS - (TOTAL_LIAB + TOTAL_EQUITY)", "diff": "2000.00",
         "tolerance": "100.00", "severity": "HIGH",
         "evidence": {"file": "a.docx", "locator": "表#92/第45行", "raw_text": "资产总计 …"}},
        {"rule_id": "BS02@1.0", "dimension": "C内", "status": "PASS", "period": "2024-12-31"},
    ]}, ensure_ascii=False), encoding="utf-8")
    (base / "rules" / "csi.json").write_text(json.dumps({
        "csi": 87.5, "grade": "B", "grade_label": "基本一致",
        "weights_used": {"C内": 0.25}, "weights_normalized": {"C内": 1.0},
        "dimensions": {"C内": {"score": 0.875, "rules_applicable": 2, "pass": 1,
                               "fail": 1, "review": 0, "na": 0}},
        "na_dimensions": [], "note": ""}, ensure_ascii=False), encoding="utf-8")
    # 现场解析产物（有它才会出现「提取字段数」与总评里的字段计数）
    (base / "ingested").mkdir(parents=True, exist_ok=True)
    (base / "ingested" / "sample.json").write_text(json.dumps([
        {"item_code": "TOTAL_ASSETS", "item_raw": "资产总计", "period": "2024-12-31",
         "period_type": "BAL_END", "value": "1000", "page": 9, "raw_text": "资产总计 1000"},
        {"item_code": "TOTAL_LIAB", "item_raw": "负债合计", "period": "2024-12-31",
         "period_type": "BAL_END", "value": "400", "page": 9, "raw_text": "负债合计 400"},
    ], ensure_ascii=False), encoding="utf-8")
    # C研 维度比对结果：一条不一致 + 一条待复核，供「研报差异点评」渲染
    (base / "rules" / "research_claims.json").write_text(json.dumps({
        "results": [
            {"rule_id": "RC01@v1.0", "dimension": "C研", "claim_id": "c1", "status": "FAIL",
             "item_code": "TOTAL_REVENUE", "period": "2024",
             "left": {"source": "research_report", "raw": "1741.4 亿元", "value": "174140000000"},
             "right": {"item_code": "TOTAL_REVENUE", "period": "2024", "value": "170000000000"},
             "diff": "4140000000", "tolerance": "1000", "severity": "HIGH",
             "cause": "VALUE_ERROR", "reason": "研报值 / 财报值 = 1.0244，非 10 的整数次幂",
             "evidence": {"file": "b.docx", "section": "核心观点",
                          "sentence": "公司2024年营业总收入1741.4亿元",
                          "verified": True}},
        ],
        "review": [{"claim_id": "c2", "reason": "模型未返回该条的匹配结果",
                    "sentence": "预计2025年收入增长8%"}],
    }, ensure_ascii=False), encoding="utf-8")


def test_report_build_all_three_formats():
    """三种报告都能生成，且都含免责声明（interfaces.md §6.3(3)）。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import DISCLAIMER, build_all

    _make_run_fixture("pytest_report")
    made = build_all("pytest_report", ("html", "xlsx", "pdf"))
    assert set(made) == {"html", "xlsx", "pdf"}
    for rel in made.values():
        assert (ROOT / rel).stat().st_size > 0

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert DISCLAIMER in html and "CSI" in html

    wb = load_workbook(ROOT / made["xlsx"])
    assert {"摘要", "异常清单", "规则明细", "提取字段"} <= set(wb.sheetnames)
    assert any(DISCLAIMER in str(c.value) for row in wb["摘要"].iter_rows()
               for c in row if c.value)

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert DISCLAIMER in text and "CSI" in text


def test_report_includes_summary_and_research_review():
    """报告必须给出成段结论：核查总评 + 研报观点与财报数字差异点评（三种格式都有）。"""
    from openpyxl import load_workbook
    from src.pipeline import ROOT
    from src.report import build_all

    _make_run_fixture("pytest_report_review")
    made = build_all("pytest_report_review", ("html", "xlsx", "pdf"))

    html = (ROOT / made["html"]).read_text(encoding="utf-8")
    assert "核查总评" in html
    # 总评必须真的复述程序产物里的数字，而不是空话
    assert "提取结构化字段 2 条" in html
    assert "异常集中在 表内勾稽 1 条" in html
    assert "CSI = 87.5" in html
    assert "等级 B（基本一致）" in html
    assert "处置建议" in html
    # 研报差异点评必须带上研报原句与差异，便于人工追溯
    assert "研报观点与财报数字差异点评" in html
    assert "公司2024年营业总收入1741.4亿元" in html
    assert "TOTAL_REVENUE" in html

    wb = load_workbook(ROOT / made["xlsx"])
    summary_cells = [str(c.value) for row in wb["摘要"].iter_rows() for c in row if c.value]
    assert any(v.startswith("核查总评") for v in summary_cells)
    assert any("研报差异点评" == v for v in summary_cells)
    assert any("提取结构化字段 2 条" in v for v in summary_cells)

    with __import__("pdfplumber").open(ROOT / made["pdf"]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "核查总评" in text and "研报观点与财报数字差异点评" in text
    assert "1741.4" in text


def test_na_dimensions_get_zero_normalized_weight():
    """不适用维度不得显示非零归一化权重——否则报告里会出现误导性的权重数字。"""
    from src.rules_engine import compute_csi
    cfg = {"version": "1.0", "dimensions": {
        "C内": {"weight": 0.25}, "C期": {"weight": 0.15},
        "C间": {"weight": 0.30}, "C附": {"weight": 0.20}, "C研": {"weight": 0.10}}}
    r = compute_csi([{"dimension": "C内", "status": "PASS"},
                     {"dimension": "C期", "status": "PASS"}], cfg)
    for na in ("C间", "C附", "C研"):
        assert r["weights_normalized"][na] == 0.0, na
    assert abs(sum(r["weights_normalized"].values()) - 1.0) < 1e-6


# ---------- H：网页入口 ----------

def test_web_app_renders_without_exception():
    """网页入口必须能渲染且不抛异常（用 Streamlit 官方测试框架真跑一遍）。"""
    from streamlit.testing.v1 import AppTest
    from src.pipeline import ROOT

    _make_run_fixture("pytest_web")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert [t.value for t in at.title] == ["财报一致性量化核查智能体"]

    labels = [m.label for m in at.metric]
    assert "CSI" in labels and "提取字段" in labels
    assert len(at.tabs) == 5

    # 切到有报告产物的运行，应出现下载按钮
    at.sidebar.selectbox[0].set_value("pytest_web").run()
    assert not at.exception, [str(e.value) for e in at.exception]


def test_web_app_handles_run_without_report():
    """没有报告产物的运行不得报错，而应提示如何去生成。"""
    from streamlit.testing.v1 import AppTest
    from src.pipeline import ROOT

    _make_run_fixture("pytest_web_noreport")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run()
    at.sidebar.selectbox[0].set_value("pytest_web_noreport").run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert any("report" in str(i.value) for i in at.info)


def test_web_upload_orchestrator_persists_and_runs(monkeypatch):
    """网页表单共用的上传编排应抽取、运行离线核查并请求三种报告。"""
    from pathlib import Path
    from src import ingest, pipeline, report
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_orchestrator"
    run_root = ROOT / "runs" / run_id
    if run_root.exists():
        import shutil
        shutil.rmtree(run_root)

    calls = {}

    class Upload:
        name = "sample_2024.pdf"

        def getvalue(self):
            return b"local-pdf-payload"

    def fake_ingest(folder, out_dir, default_year, report_period=None):
        calls["uploaded"] = (folder / Upload.name).read_bytes()
        calls["year"] = default_year
        calls["period"] = report_period
        return {"structured_files": [out_dir / "sample.json"], "claims_path": None}

    def fake_pipeline(config, **kwargs):
        calls["pipeline"] = (config, kwargs)

    def fake_reports(run, formats):
        calls["reports"] = (run, formats)
        return {f: f"runs/{run}/report.{f}" for f in ("pdf", "xlsx", "html")}

    monkeypatch.setattr(ingest, "process_folder", fake_ingest)
    monkeypatch.setattr(pipeline, "run", fake_pipeline)
    monkeypatch.setattr(report, "build_all", fake_reports)
    progress = []

    try:
        result = run_uploaded_reports(
            [Upload()], run_id=run_id, default_year=2024, offline=True,
            on_progress=progress.append,
        )
        assert calls["uploaded"] == b"local-pdf-payload"
        assert calls["year"] == 2024
        assert calls["period"] == "annual"
        assert calls["pipeline"][1]["offline"] is True
        assert calls["pipeline"][1]["skip_claims"] is True
        assert calls["reports"] == (run_id, ("pdf", "xlsx", "html"))
        assert set(result["reports"]) == {"pdf", "xlsx", "html"}
        assert len(progress) == 3
    finally:
        import shutil
        shutil.rmtree(run_root, ignore_errors=True)


def test_web_upload_rejects_duplicate_names_before_creating_run():
    """无效上传在验证失败时不应留下半成品运行目录。"""
    import shutil
    import pytest
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_duplicate"
    run_root = ROOT / "runs" / run_id
    shutil.rmtree(run_root, ignore_errors=True)

    class Upload:
        def __init__(self, name):
            self.name = name

        def getvalue(self):
            return b"file"

    with pytest.raises(ValueError, match="文件名重复"):
        run_uploaded_reports(
            [Upload("Report.pdf"), Upload("report.PDF")],
            run_id=run_id, default_year=2024, offline=True,
        )
    assert not run_root.exists()


def test_web_upload_unknown_file_still_builds_report(monkeypatch):
    """上传「任意 PDF/DOCX」而一份年报都没识别出来时不得报错，仍要产出三种报告。

    异常什么结果都不给，用户无从判断；正确行为是照常跑完流程，产出一份
    "未识别到可核查字段"的说明性报告。
    """
    import shutil
    from src import ingest, pipeline, report
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_no_fields"
    run_root = ROOT / "runs" / run_id
    shutil.rmtree(run_root, ignore_errors=True)
    seen = {}

    class Upload:
        name = "随便一份.pdf"

        def getvalue(self):
            return b"payload"

    monkeypatch.setattr(ingest, "process_folder",
                        lambda folder, out_dir, default_year, report_period=None: {
                            "structured_files": [], "claims_path": None})
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: seen.update(k))
    monkeypatch.setattr(report, "build_all",
                        lambda r, f: {x: f"runs/{r}/report.{x}" for x in f})

    try:
        out = run_uploaded_reports([Upload()], run_id=run_id, default_year=2024, offline=True)
        assert set(out["reports"]) == {"pdf", "xlsx", "html"}
        assert seen, "没识别出年报也必须跑完流程再出报告"
        assert seen["data_dir_override"].replace("\\", "/") == f"runs/{run_id}/ingested"
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_web_upload_threads_report_period(monkeypatch):
    """网页上传必须把「报告期间」透传到解析器，并拒绝非法取值。

    漏传会退回默认 annual：半年报被按年报列头抽取、流量科目标成全年 FY，
    与用户上传的文件对不上——这正是"传进去的报告结论全错"的根因之一。
    """
    import shutil
    import pytest
    from src import ingest, pipeline, report
    from src.web import ROOT, run_uploaded_reports

    run_id = "pytest_web_upload_half_year"
    run_root = ROOT / "runs" / run_id
    shutil.rmtree(run_root, ignore_errors=True)
    seen = {}

    class Upload:
        name = "贵州茅台2026年半年度报告.pdf"

        def getvalue(self):
            return b"payload"

    def fake_ingest(folder, out_dir, default_year, report_period=None):
        seen["period"] = report_period
        return {"structured_files": [], "claims_path": None}

    monkeypatch.setattr(ingest, "process_folder", fake_ingest)
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    monkeypatch.setattr(report, "build_all", lambda r, f: {})

    try:
        run_uploaded_reports([Upload()], run_id=run_id, default_year=2026,
                             offline=True, report_period="half_year")
        assert seen["period"] == "half_year"
        with pytest.raises(ValueError, match="不支持的报告期间"):
            run_uploaded_reports([Upload()], run_id="pytest_web_upload_bad_period",
                                 default_year=2026, offline=True,
                                 report_period="quarterly")
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_ingest_without_annual_never_falls_back_to_shared_data(monkeypatch):
    """CLI 文件夹入口没识别出年报时，必须仍指向本轮 ingested 目录。

    若回落成 None，pipeline 会去读共享的 data/structured/（旧基线数据），
    产出一份与本次上传文件毫无关系、还看着"很正常"的报告——比空报告更误导人。
    """
    from src import ingest
    import src.pipeline as P

    seen = {}
    monkeypatch.setattr(ingest, "process_folder",
                        lambda folder, out_dir, default_year, report_period: {
                            "files": 1, "log": [], "structured_files": [],
                            "claims_path": None, "claims_count": 0})
    monkeypatch.setattr(P, "run", lambda *a, **k: seen.update(k))

    ingest.ingest("does-not-matter", "pytest_ingest_no_fields", offline=True)
    assert seen["data_dir_override"].replace("\\", "/") == "runs/pytest_ingest_no_fields/ingested"
    assert seen["claims_override"] is None and seen["skip_claims"] is True


def test_csi_includes_research_dimension():
    """C研 结果必须并入 CSI，并按 rules.yaml 的当前维度权重计算。

    此前 C研 的 FAIL 不进 CSI，会出现"查出 2 条研报错误、CSI 却仍是满分"的虚高。
    """
    from src.pipeline import ROOT
    from src.rules_engine import run_rules

    base = ROOT / "runs" / "pytest_csi_research" / "rules"
    base.mkdir(parents=True, exist_ok=True)
    (base / "research_claims.json").write_text(json.dumps({"results": [
        {"dimension": "C研", "status": "PASS"}, {"dimension": "C研", "status": "FAIL"},
    ]}, ensure_ascii=False), encoding="utf-8")

    out = run_rules([
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "资产总计 1000.00"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "400.00", "unit_raw": "元", "raw_text": "负债合计 400.00"},
        {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "600.00", "unit_raw": "元", "raw_text": "所有者权益合计 600.00"},
    ], "pytest_csi_research")

    dims = out["csi"]["dimensions"]
    assert dims["C研"]["rules_applicable"] == 2, "C研 结果未并入 CSI"
    assert dims["C研"]["score"] == 0.5
    assert out["csi"]["csi"] < 100, "研报有 FAIL 时 CSI 不应满分"


def test_csi_grade_boundaries():
    """等级按接口的 90/75/60/40 分边界映射，低于40为E。"""
    from src.rules_engine import compute_csi

    cfg = {"version": "1.0", "dimensions": {"C内": {"weight": 1.0}}}
    for score, expected in ((90.0, "A"), (89.5, "B"), (75.0, "B"),
                            (74.5, "C"), (60.0, "C"), (59.5, "D"),
                            (40.0, "D"), (39.5, "E")):
        passes = int(score)
        reviews = int((score - passes) * 2)
        results = ([{"dimension": "C内", "status": "PASS"}] * passes
                   + [{"dimension": "C内", "status": "REVIEW"}] * reviews
                   + [{"dimension": "C内", "status": "FAIL"}] * (100 - passes - reviews))
        got = compute_csi(results, cfg)
        assert got["csi"] == score, (score, got["csi"])
        assert got["grade"] == expected, (score, got["grade"])


def _provisional_records():
    return [
        {"item_code": "TOTAL_ASSETS", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "1000.00", "unit_raw": "元", "raw_text": "资产总计 1000.00"},
        {"item_code": "TOTAL_LIAB", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "400.00", "unit_raw": "元", "raw_text": "负债合计 400.00"},
        {"item_code": "TOTAL_EQUITY", "period": "2024-12-31", "period_type": "BAL_END",
         "value": "600.00", "unit_raw": "元", "raw_text": "所有者权益合计 600.00"},
    ]


def test_csi_weights_come_from_central_scoring_config():
    """CSI 权重必须来自 rules.yaml 的 scoring 段（集中开关），weights_used 与配置一致。"""
    import yaml
    from src.pipeline import ROOT
    from src.rules_engine import run_rules

    cfg = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    expected = {k: v["weight"] for k, v in cfg["scoring"]["dimensions"].items()}

    run_rules(_provisional_records(), "pytest_scoring_cfg")
    csi = json.loads((ROOT / "runs" / "pytest_scoring_cfg" / "rules" / "csi.json")
                     .read_text(encoding="utf-8"))
    assert csi["weights_used"] == expected, \
        "weights_used 未取自集中在 rules.yaml 的 scoring 配置"


def test_csi_json_marks_provisional():
    """口径未获裁定前，csi.json 必须带 provisional 标记与生效口径来源（profile）。"""
    import yaml
    from src.pipeline import ROOT
    from src.rules_engine import run_rules

    cfg = yaml.safe_load((ROOT / "rules" / "rules.yaml").read_text(encoding="utf-8"))
    run_rules(_provisional_records(), "pytest_scoring_cfg")
    csi = json.loads((ROOT / "runs" / "pytest_scoring_cfg" / "rules" / "csi.json")
                     .read_text(encoding="utf-8"))
    assert csi["provisional"] is True, "csi.json 缺少 provisional 标记"
    assert csi["profile"] == cfg["scoring"]["profile"] == "default-provisional"


# ---------- I：异常原因解释 ----------

def _fake_explain(monkeypatch, cause="UNIT_SCALE", text="单位表述不规范", conf=0.9):
    from src import explain
    seen = []

    def fake(task, payload, run_id, **kw):
        seen.append((task, payload))
        return ({"ok": True, "cause": cause, "explanation": text, "confidence": conf,
                 "evidence": {"chunk_id": payload["chunk_id"], "quote": ""}}, "API_OK")

    monkeypatch.setattr(explain, "call_task", fake)
    return explain, seen


def test_explain_only_for_fail_and_review(monkeypatch):
    """只解释 FAIL / REVIEW；PASS 与 NA 不调用模型（省钱且无意义）。"""
    explain, seen = _fake_explain(monkeypatch)
    results = [
        {"rule_id": "A", "status": "PASS", "evidence": {"chunk_id": "t1", "raw_text": "x"}},
        {"rule_id": "B", "status": "NA", "evidence": {"chunk_id": "t2", "raw_text": "x"}},
        {"rule_id": "C", "status": "FAIL", "diff": "2000.00", "tolerance": "100.00",
         "evidence": {"chunk_id": "t3", "raw_text": "资产总计 …"}},
    ]
    explain.explain_anomalies(results, "pytest_explain")
    assert len(seen) == 1 and seen[0][1]["rule_id"] == "C"


def test_explain_rejects_numbers_in_explanation(monkeypatch):
    """模型解释里出现数字即丢弃——prompt 已禁止，程序侧必须兜底。"""
    explain, _ = _fake_explain(monkeypatch, text="差额约为财报值的10倍")
    results = [{"rule_id": "C", "status": "FAIL", "diff": "2000", "tolerance": "100",
                "evidence": {"chunk_id": "t3", "raw_text": "x"}}]
    explain.explain_anomalies(results, "pytest_explain2")
    e = results[0]["explanation"]
    assert e["text"] == "" and "含数字" in e["note"]


def test_explain_flags_disagreement_with_program():
    """模型判断与程序确定性归因不一致时必须标出，不得静默采信。"""
    from src import explain
    import src.explain as E

    class FakeCall:
        @staticmethod
        def call_task(task, payload, run_id, **kw):
            return ({"ok": True, "cause": "VALUE_ERROR", "explanation": "疑为数值引用错误",
                     "confidence": 0.8,
                     "evidence": {"chunk_id": payload["chunk_id"], "quote": ""}}, "API_OK")

    orig = E.call_task
    E.call_task = FakeCall.call_task
    try:
        results = [{"rule_id": "RC01", "status": "FAIL", "cause": "UNIT_SCALE",
                    "diff": "1", "tolerance": "10", "claim_id": "c1",
                    "evidence": {"sentence": "归母净利润 862.3 亿万元"}}]
        explain.explain_anomalies([], "pytest_explain3", research_results=results)
        e = results[0]["explanation"]
        assert e["program_cause"] == "UNIT_SCALE"
        assert e["cause"] == "VALUE_ERROR"
        assert e["agrees_with_program"] is False
    finally:
        E.call_task = orig


def test_diff_desc_is_neutral_for_bad_unit():
    """畸形单位时的差异描述：既不把模型引向"数值差异"，也不直接泄露结论。

    正例："差异不在数值大小上" —— 告诉模型别盯着数字，但不点名是单位问题；
    模型要自己从证据原文（含"亿万元"）推出 UNIT_SCALE。
    """
    from src.explain import _diff_desc
    d = _diff_desc({"reason": "单位写法非法：亿万元", "diff": "1", "tolerance": "10"})
    assert "略超容差" not in d, "不得暗示这是数值大小问题"
    assert "数值" in d, "应明确说明差异不在数值上"
    assert "UNIT_SCALE" not in d and "单位写法非法" not in d, "不得直接把答案写进描述"


# ---------- J：标准答案与准确率 / 可复现性 ----------

def test_accuracy_compare_counts_correct_wrong_missing():
    """准确率 = 正确 ÷ 应提取；缺失与错误分别计数，不混为一谈。"""
    from tools.check_accuracy import compare
    gold = {"entries": [
        {"item_code": "A", "item_raw": "甲", "period": "2024", "value": "100.00",
         "basis": "双源一致"},
        {"item_code": "B", "item_raw": "乙", "period": "2024", "value": "200.00",
         "basis": "仅PDF"},
        {"item_code": "C", "item_raw": "丙", "period": "2024", "value": "300.00",
         "basis": "仅PDF"},
    ]}
    records = [
        {"item_code": "A", "period": "2024", "value": "100.00"},   # 正确
        {"item_code": "B", "period": "2024", "value": "999.00"},   # 错误
        # C 缺失
    ]
    r = compare(gold, records)
    assert r["total"] == 3 and r["correct"] == 1
    assert r["wrong"] == 1 and r["missing"] == 1
    assert r["E"] == round(1 / 3 * 100, 2)


def test_accuracy_matches_any_duplicate_value():
    """同一 (科目,期间) 有多条记录时，任一条对上即算正确。"""
    from tools.check_accuracy import compare
    gold = {"entries": [{"item_code": "A", "item_raw": "甲", "period": "2024",
                         "value": "100.00", "basis": "仅PDF"}]}
    records = [{"item_code": "A", "period": "2024", "value": "0"},
               {"item_code": "A", "period": "2024", "value": "100.00"}]
    assert compare(gold, records)["E"] == 100.0


def test_gold_standard_exists_and_is_dual_sourced():
    """标准答案必须存在，且含双来源交叉确认的条目（否则不独立）。"""
    from src.pipeline import ROOT
    p = ROOT / "data" / "gold_standard" / "贵州茅台_2024_核心字段.json"
    if not p.exists():
        return
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["entries"], "标准答案为空"
    assert not d.get("mismatches"), "标准答案两个来源存在冲突，须人工裁定"
    dual = [e for e in d["entries"] if e["basis"].startswith("双源")]
    assert dual, "没有任何双来源交叉确认的条目，独立性问题"
    for e in d["entries"]:
        assert e["evidence"].get("page") is not None, f"{e['item_code']} 缺证据页码"


def test_reproducible_digest_ignores_run_id():
    """可复现性比对必须剔除 run_id——它是运行标识，本就该不同。"""
    import tempfile
    from pathlib import Path as _P
    from tools.check_reproducible import _digest
    d = _P(tempfile.mkdtemp())
    (d / "a.json").write_text('{"run_id":"X","v":[1,2]}', encoding="utf-8")
    (d / "b.json").write_text('{"run_id":"Y","v":[1,2]}', encoding="utf-8")
    (d / "c.json").write_text('{"run_id":"X","v":[1,3]}', encoding="utf-8")
    assert _digest(d / "a.json") == _digest(d / "b.json")
    assert _digest(d / "a.json") != _digest(d / "c.json")


def test_save_manifest_marks_completed(tmp_path, monkeypatch):
    """正常结束落盘后应把 RUNNING 状态收敛为 COMPLETED。"""
    import src.manifest as manifest

    monkeypatch.setattr(manifest, "ROOT", tmp_path)
    monkeypatch.setattr(manifest, "summarize_calls", lambda *_: {
        "api_ok": 0, "cache_hit": 0, "cache_miss": 0, "failed": 0, "total": 0,
    })
    data = {"run_id": "manifest_test", "started_at": "2026-10-12T00:00:00+08:00",
            "status": "RUNNING"}
    path = manifest.save_manifest("manifest_test", data)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "COMPLETED"
    assert saved["finished_at"]


def test_error_fixture_meets_interface_contract():
    """植错研报 fixture 有 8–10 条、五类各至少两条，且 CSV 列与契约一致。"""
    import csv
    from tools.build_error_case_fixture import CASES, validate_cases
    from src.pipeline import ROOT

    assert len(CASES) == 10
    assert set(validate_cases(CASES).values()) == {2}
    path = ROOT / "data" / "demo" / "研报错误测试案例_契约核验版.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == ["claim_id", "错误句", "错误类型", "标准答案", "证据应在页码", "备注"]
    assert len(rows) == 10
    assert {r["错误类型"] for r in rows} == {
        "UNIT_SCALE", "PERIOD_MISMATCH", "SCOPE_MIXED", "VALUE_ERROR", "SIGN_ERROR",
    }
    assert len({r["claim_id"] for r in rows}) == 10
