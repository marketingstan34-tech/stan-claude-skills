"""SYNTHETIC end-to-end test of the cassation pipeline with mocked OpenAI and VKS."""

import json
from datetime import date

import httpx
import pytest

from legal_ai.ai import AIConfig, OpenAIProvider
from legal_ai.cassation.pipeline import SourceDoc, check_quote, render_markdown, run_analysis
from legal_ai.citations.verify import locate_quote
from legal_ai.http import PoliteClient

APPELLATE = ("СИНТЕТИЧНО ВЪЗЗИВНО РЕШЕНИЕ.\nСъдът приема, че волята на третото лице не обвързва "
             "кредитора.\nРешението подлежи на обжалване.")

VKS_LIST = ('<a href="pregled-akt.jsp?type=ot-spisak&id=' + "A" * 32 +
            '">Решение №1/01.02.2015 по дело №10/2014</a>')
VKS_ACT = """<html><body><div id="Content" class="AktSadarjanie">
Р Е Ш Е Н И Е<br>№ 1<br>София, 01.02.2015 г.<br>
Върховният касационен съд, Търговска колегия, Второ отделение<br>
Производството е по чл. 290 ГПК.<br>
Касационното обжалване е допуснато по въпроса за волята на платеца.<br>
Меродавна за това е волята на платеца.<br>
Р Е Ш И: ОТМЕНЯ.<br></div></body></html>"""


def _ai_transport():
    answers = [
        {"case_summary": "Синтетичен казус.",
         "lower_instance": {"act": "Решение № 1", "date": "01.01.2021", "case": "т.д. 1/2020", "court": "ОС"},
         "holdings": [{"id": "H1", "summary": "Волята на третото лице не обвързва кредитора.",
                       "quote": "волята на третото лице не обвързва кредитора"},
                      {"id": "H2", "summary": "Измислен цитат.", "quote": "това го няма в текста"}],
         "questions": [{"id": "В1", "text": "Обвързва ли кредитора изборът на платеца?",
                        "kind": "материалноправен", "holding_ids": ["H1"], "ground": "т.1",
                        "why_decisive": "Изходът зависи от това."}],
         "search": [{"question_id": "В1", "word_sets": [["волята", "платеца"]]}]},
        {"relevant": True, "stance": "подкрепя", "vks_rule": "Филтър.", "quote": "", "explanation": "-"},
        {"relevant": True, "stance": "противоречи", "vks_rule": "Меродавна е волята на платеца.",
         "quote": "Меродавна за това е волята на платеца.", "explanation": "Обратно на въззивния съд."},
    ]
    calls = []
    pending = []

    def body_ok(request):
        body = json.loads(request.content)
        return body["text"]["format"]["strict"] is True and body["background"] is True

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=pending.pop())
        assert body_ok(request)
        body = json.loads(request.content)
        calls.append(body)
        out = answers[len(calls) - 1]
        pending.append({
            "id": f"resp_{len(calls)}", "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 5},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(out)}]}]})
        return httpx.Response(200, json={"id": f"resp_{len(calls)}", "status": "queued"})
    return httpx.MockTransport(handler), calls


def _vks_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if "spisak-aktove.jsp" in str(request.url):
            return httpx.Response(200, text=VKS_LIST)
        return httpx.Response(200, text=VKS_ACT)
    return httpx.MockTransport(handler)


def test_pipeline_verifies_quotes_and_groups_by_stance():
    transport, calls = _ai_transport()
    ai = OpenAIProvider(AIConfig("model-a", "model-b", max_calls=10, assess_model="model-c", stance_model="model-a"), transport=transport, sleep=lambda s: None)
    vks = PoliteClient(["www.vks.bg"], transport=_vks_transport(), sleep=lambda s: None)
    doc = SourceDoc("Синтетично дело", "file:///synthetic", APPELLATE, "txt", "2026-01-01")
    r = run_analysis(ai, vks, doc, date(2022, 1, 1))

    assert r.holding_quotes["H1"].status == "text_verified"
    assert r.holding_quotes["H2"].status == "not_found"
    assert len(r.assessments) == 1
    a = r.assessments[0]
    assert a.stance == "противоречи" and a.quote.status == "text_verified"
    assert a.proceeding_article == "290"
    assert r.usage["calls"] == 3
    # analysis, then relevance filter (cheap), then stance by the stronger model
    assert [c["model"] for c in calls] == ["model-a", "model-c", "model-a"]
    assert calls[1]["reasoning"]["effort"] == "low"
    assert r.assessments[0].stage == "посока"

    md = render_markdown(r)
    assert "Противоречи на въззивния съд" in md
    assert "✅ цитатът е проверен" in md and "⚠️ цитатът НЕ е намерен" in md


def test_ai_call_cap_is_enforced():
    transport, _ = _ai_transport()
    ai = OpenAIProvider(AIConfig("m", "m", max_calls=0), transport=transport)
    import pytest
    from legal_ai.ai import AIError
    with pytest.raises(AIError):
        ai.structured(model="m", system="s", user="u", schema_name="x", schema={"type": "object"})


def test_locate_quote_tolerates_only_whitespace():
    text = "Първо изречение.\nМеродавна   за това\nе волята на платеца. Край."
    loc = locate_quote(text, "Меродавна за това е волята на платеца.")
    assert loc is not None and text[loc[0]:loc[1]].startswith("Меродавна")
    assert locate_quote(text, "Меродавна е волята на платеца.") is None
    assert check_quote(text, "волята на платеца").status == "text_verified"
    assert check_quote(text, "волята на длъжника").status == "not_found"


def test_non_contiguous_sentences_are_verified_separately_and_marked():
    text = "Първо изречение тук. Пропуснато изречение по средата. Трето изречение накрая."
    q = check_quote(text, "Първо изречение тук. Трето изречение накрая.")
    assert q.status == "text_verified"
    assert q.text == "Първо изречение тук. […] Трето изречение накрая."
    assert check_quote(text, "Трето изречение накрая. Първо изречение тук.").status == "not_found"
    assert check_quote(text, "Първо изречение тук. Измислено изречение.").status == "not_found"


def test_exhausted_credit_stops_with_a_clear_error():
    import pytest
    from legal_ai.ai import AIQuotaError

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"id": "r", "status": "failed",
                                             "error": {"code": "credit_balance_exhausted", "message": "x"}})
        return httpx.Response(200, json={"id": "r", "status": "queued"})

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=10), transport=httpx.MockTransport(handler),
                        sleep=lambda s: None)
    with pytest.raises(AIQuotaError):
        ai.structured(model="m", system="s", user="u", schema_name="x", schema={"type": "object"})


def test_claude_provider_structured_output_usage_and_errors(monkeypatch):
    """Claude via the SDK: JSON-schema output, effort, refusal fallback, call cap, quota stop."""
    from types import SimpleNamespace

    import anthropic

    from legal_ai.ai import AIError, AIQuotaError, load_ai_config, make_provider
    from legal_ai.ai.claude import FALLBACK_BETA, ClaudeProvider

    sent = []

    class FakeStream:
        def __init__(self, msg):
            self.msg = msg
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get_final_message(self):
            if isinstance(self.msg, Exception):
                raise self.msg
            return self.msg

    def message(text, stop="end_turn", model="claude-opus-5-5"):
        return SimpleNamespace(model=model, stop_reason=stop, content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                     cache_creation_input_tokens=0, cache_read_input_tokens=5))

    replies = []

    class FakeClient:
        beta = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: (sent.append(kw), FakeStream(replies.pop(0)))[1]))
        def close(self):
            pass

    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    for var in ("CLAUDE_ANALYSIS_MODEL", "CLAUDE_QUERY_MODEL", "CLAUDE_ASSESS_MODEL", "CLAUDE_STANCE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_ai_config()
    assert (cfg.provider, cfg.analysis_model, cfg.assess_model, cfg.stance_model) == \
        ("anthropic", "claude-opus-5-5", "claude-haiku-5-5", "claude-opus-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    assert isinstance(make_provider(cfg), ClaudeProvider)

    ai = ClaudeProvider(cfg, client=FakeClient())
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}
    replies.append(message('{"a": 1}'))
    assert ai.structured(model="claude-opus-5-5", system="S", user="U", schema_name="x", schema=schema, effort="low") == {"a": 1}
    kw = sent[-1]
    assert kw["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": schema}}
    assert kw["fallbacks"] == "default" and kw["betas"] == [FALLBACK_BETA] and kw["system"] == "S"
    assert ai.usage.by_model["claude-opus-5-5"] == [105, 20]
    # the fast filter model has no refusal fallback; OpenAI's "minimal" effort becomes "low"
    replies.append(message('{"a": 2}', model="claude-haiku-5-5"))
    ai.structured(model="claude-haiku-5-5", system="S", user="U", schema_name="x", schema=schema, effort="minimal")
    assert "fallbacks" not in sent[-1] and sent[-1]["output_config"]["effort"] == "low"
    # a cut-off answer is asked once more; a refusal is an error
    replies.extend([message('{"a"', stop="max_tokens"), message('{"a": 3}')])
    assert ai.structured(model="claude-opus-5-5", system="S", user="U", schema_name="x", schema=schema)["a"] == 3
    replies.append(message("", stop="refusal"))
    with pytest.raises(AIError, match="отказа"):
        ai.structured(model="claude-opus-5-5", system="S", user="U", schema_name="x", schema=schema)
    # no credit: the whole run stops
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    replies.append(anthropic.PaymentRequiredError("no credit", response=httpx.Response(402, request=req), body=None))
    with pytest.raises(AIQuotaError, match="Anthropic"):
        ai.structured(model="claude-opus-5-5", system="S", user="U", schema_name="x", schema=schema)
    # the call cap counts every request
    capped = ClaudeProvider(AIConfig("m", "m", max_calls=0, provider="anthropic"), client=FakeClient())
    with pytest.raises(AIError, match="лимит"):
        capped.structured(model="claude-opus-5-5", system="S", user="U", schema_name="x", schema=schema)


def test_provider_choice_and_prices(monkeypatch):
    from legal_ai.ai import AIError, load_ai_config, provider_name
    from legal_ai.ai.pricing import cost_usd, report_usd, vendor
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert provider_name() == "openai" and vendor()["name"] == "OpenAI"
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    assert vendor()["name"] == "Anthropic" and report_usd()[1] < 1.4
    assert abs(cost_usd({"claude-opus-5-5": [1_000_000, 100_000], "claude-haiku-5-5": [1_000_000, 0]}) - 6.10) < 1e-9
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    with pytest.raises(AIError):
        load_ai_config()
