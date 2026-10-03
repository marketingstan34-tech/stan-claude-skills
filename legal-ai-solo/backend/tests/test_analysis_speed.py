"""SYNTHETIC tests for the speed options of the cassation analysis.

No network: OpenAI and vks.bg are httpx.MockTransport fakes, sleep is a no-op.
"""

import json
import threading
import time
from dataclasses import asdict
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from legal_ai.ai import POLL_BACKOFF, AIConfig, AIError, AIQuotaError, OpenAIProvider, runtime_env
from legal_ai.cassation import pipeline
from legal_ai.cassation.pipeline import (
    SourceDoc,
    local_first_skips,
    render_markdown,
    run_analysis,
    search_vks,
)
from legal_ai.http import PoliteClient

# ---------- synthetic VKS site ----------

ACTS = {  # key -> (number, date, chamber line, article line, body)
    "B": (2, "01.02.2017", "Търговска колегия, Второ отделение", "Производството е по чл. 290 ГПК.",
          "Изборът на платеца не обвързва кредитора."),
    "A": (1, "01.02.2015", "Търговска колегия, Първо отделение", "Производството е по чл. 290 ГПК.",
          "Меродавна за това е волята на платеца."),
    "E": (5, "01.02.2020", "Гражданска колегия, Първо гражданско отделение",
          "Производството е по чл. 290 ГПК.", "Не се сваля."),
    "D": (7, "01.02.2016", "Гражданска колегия, Трето гражданско отделение",
          "Производството е по чл. 290 ГПК.", "Давността тече от изискуемостта на вземането."),
    "C": (3, "01.02.2019", "Наказателна колегия, Първо наказателно отделение",
          "Производството е по чл. 354 НПК.", "Наказателно дело."),
}
LISTS = {  # words -> acts in the result list
    "волята платеца": ["A", "B", "E"],
    "избор платеца": ["A", "B"],
    "давност вземане": ["C", "A", "D"],
    "давност платеца": ["A", "D"],
}


def sid(key: str) -> str:
    return key * 32


def _list_html(keys):
    return "".join(f"<a href='pregled-akt.jsp?type=ot-spisak&id={sid(k)}'>Решение №{ACTS[k][0]}/"
                   f"{ACTS[k][1]} по дело №{ACTS[k][0] * 10}/2014</a>" for k in keys)


def _act_html(key):
    no, d, chamber, article, body = ACTS[key]
    return (f'<html><body><div id="Content" class="AktSadarjanie">Р Е Ш Е Н И Е<br>№ {no}<br>'
            f"София, {d} г.<br>Върховният касационен съд, {chamber}<br>{article}<br>{body}<br>"
            "Р Е Ш И: ОТМЕНЯ.<br></div></body></html>")


class FakeVKS:
    """Records every request and the thread it came from; the act E is missing (404)."""

    def __init__(self, wait_for_ai: threading.Event | None = None):
        self.urls: list[str] = []
        self.threads: set[int] = set()
        self.in_flight = 0
        self.max_in_flight = 0
        self.wait_for_ai = wait_for_ai
        self.ai_started_before_last_download = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            url = str(request.url)
            self.urls.append(url)
            self.threads.add(threading.get_ident())
            if "spisak-aktove.jsp" in url:
                words = parse_qs(urlparse(url).query)["AktDumiVSadarjanie"][0]
                return httpx.Response(200, text=_list_html(LISTS.get(words, [])))
            key = parse_qs(urlparse(url).query)["id"][0][0]
            if key == "E":
                return httpx.Response(404, text="няма")
            if key == "C" and self.wait_for_ai is not None:  # the last download of the run
                self.ai_started_before_last_download = self.wait_for_ai.wait(5)
            return httpx.Response(200, text=_act_html(key))
        finally:
            self.in_flight -= 1

    def client(self) -> PoliteClient:
        return PoliteClient(["www.vks.bg"], transport=httpx.MockTransport(self.handler),
                            sleep=lambda s: None)


# ---------- synthetic OpenAI (answers depend on the prompt only, not on call order) ----------

ANALYSIS = {
    "case_summary": "Синтетичен казус.",
    "lower_instance": {"act": "Решение № 1", "date": "01.01.2021", "case": "т.д. 1/2020", "court": "ОС"},
    "holdings": [{"id": "H1", "summary": "Волята на третото лице не обвързва кредитора.",
                  "quote": "волята на третото лице не обвързва кредитора"}],
    "questions": [
        {"id": "В1", "text": "Обвързва ли кредитора изборът на платеца?", "kind": "материалноправен",
         "holding_ids": ["H1"], "ground": "т.1", "why_decisive": "Изходът зависи от това."},
        {"id": "В2", "text": "Кога тече давността?", "kind": "материалноправен",
         "holding_ids": [], "ground": "т.1", "why_decisive": "Давността е възражение."}],
    "search": [{"question_id": "В1", "word_sets": [["волята", "платеца"], ["избор", "платеца"]]},
               {"question_id": "В2", "word_sets": [["давност", "вземане"], ["давност", "платеца"]]}],
}
APPELLATE = "СИНТЕТИЧНО ВЪЗЗИВНО РЕШЕНИЕ.\nСъдът приема, че волята на третото лице не обвързва кредитора."


def _answer(model: str, prompt: str) -> str:
    if "=== ВЪЗЗИВНО РЕШЕНИЕ ===" in prompt:
        return json.dumps(ANALYSIS)
    q2 = "Кога тече давността?" in prompt
    act = next(k for k, v in ACTS.items() if f"(Б) Решение №{v[0]}/" in prompt)
    if q2 and act == "D":
        return "{невалиден"  # both attempts fail -> skipped with a note
    if model == "light":  # stage 1: relevance filter
        return json.dumps({"relevant": not (act == "B"), "stance": "неясно", "vks_rule": "-",
                           "quote": "", "explanation": "филтър"})
    if act == "A" and not q2:
        return json.dumps({"relevant": True, "stance": "противоречи", "vks_rule": "Волята на платеца.",
                           "quote": "Меродавна за това е волята на платеца.", "explanation": "Обратно."})
    return json.dumps({"relevant": True, "stance": "подкрепя", "vks_rule": "Друго.",
                       "quote": "Този цитат го няма.", "explanation": "Същото."})


class FakeOpenAI:
    """Background mode: POST -> queued, first GET -> in_progress, second GET -> completed."""

    def __init__(self, delay: float = 0.0, started: threading.Event | None = None):
        self.lock = threading.Lock()
        self.pending: dict[str, list[dict]] = {}
        self.posts: list[dict] = []
        self.delay = delay
        self.started = started

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            rid = request.url.path.rsplit("/", 1)[1]
            with self.lock:
                return httpx.Response(200, json=self.pending[rid].pop(0))
        body = json.loads(request.content)
        prompt = body["input"][1]["content"]
        if self.started is not None and "=== ВЪЗЗИВНО РЕШЕНИЕ ===" not in prompt:
            self.started.set()
        if self.delay:
            time.sleep(self.delay)
        done = {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 5},
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": _answer(body["model"], prompt)}]}]}
        with self.lock:
            self.posts.append(body)
            rid = f"resp_{len(self.posts)}"
            self.pending[rid] = [{"id": rid, "status": "in_progress"}, {"id": rid, **done}]
        if not body.get("background"):
            return httpx.Response(200, json={"id": rid, **done})
        return httpx.Response(200, json={"id": rid, "status": "queued"})


def _provider(fake: FakeOpenAI, **cfg) -> OpenAIProvider:
    config = AIConfig("strong", "light", max_calls=40, assess_model="light", stance_model="strong", **cfg)
    return OpenAIProvider(config, transport=httpx.MockTransport(fake.handler), sleep=lambda s: None)


def _run(*, overlap: bool, workers: int, poll_backoff: bool, background: bool = True,
         ai_delay: float = 0.0, wait_for_ai: bool = False):
    started = threading.Event() if wait_for_ai else None
    ai_fake, vks_fake = FakeOpenAI(ai_delay, started), FakeVKS(started)
    ai = _provider(ai_fake, workers=workers, poll_backoff=poll_backoff, background=background)
    doc = SourceDoc("Синтетично дело", "file:///synthetic", APPELLATE, "txt", "2026-01-01")
    r = run_analysis(ai, vks_fake.client(), doc, date(2022, 1, 1), overlap=overlap, local_first=False)
    return r, ai_fake, vks_fake


def _comparable(r):
    return {"assessments": [asdict(a) for a in r.assessments], "skipped": r.skipped,
            "searches": r.searches, "assess_inputs": r.assess_inputs,
            "holding_quotes": {k: asdict(v) for k, v in r.holding_quotes.items()},
            "usage": r.usage, "models": r.models}


# ---------- identical results ----------

def test_speed_options_do_not_change_the_result():
    base, ai_base, vks_base = _run(overlap=False, workers=1, poll_backoff=False)
    fast, ai_fast, vks_fast = _run(overlap=True, workers=12, poll_backoff=True, ai_delay=0.01,
                                   wait_for_ai=True)
    direct, _, _ = _run(overlap=True, workers=4, poll_backoff=True, background=False)

    assert _comparable(fast) == _comparable(base)
    assert _comparable(direct) == _comparable(base)

    # the synthetic case covers every path: verified/unverified quote, filter-only, AI failure,
    # criminal act, failed download, an act shared by two questions
    by = {(a.question_id, a.source_id[0]): a for a in base.assessments}
    assert set(by) == {("В1", "B"), ("В1", "A"), ("В2", "A")}
    assert by[("В1", "A")].stance == "противоречи" and by[("В1", "A")].quote.status == "text_verified"
    assert by[("В2", "A")].quote.status == "not_found"
    assert by[("В1", "B")].stage == "филтър" and not by[("В1", "B")].relevant
    assert any("наказателно дело" in s for s in base.skipped)
    assert any(s.startswith(sid("E")) for s in base.skipped)
    assert any("AI оценката не успя" in s for s in base.skipped)
    assert base.usage["calls"] == 8

    # the VKS site sees the same requests in the same order, one at a time, from one thread
    assert vks_fast.urls == vks_base.urls
    assert sum("pregled-akt" in u for u in vks_base.urls) == 5  # A is downloaded once for both questions
    for v in (vks_base, vks_fast):
        assert len(v.threads) == 1 and v.max_in_flight == 1
    # with overlap the AI had started before the last act was downloaded
    assert vks_fast.ai_started_before_last_download is True


def test_report_is_the_same_with_and_without_speed_options():
    base, _, _ = _run(overlap=False, workers=1, poll_backoff=False)
    fast, _, _ = _run(overlap=True, workers=12, poll_backoff=True)
    fast.created_at = base.created_at
    assert render_markdown(fast) == render_markdown(base)


def test_quota_error_stops_the_run_and_queued_assessments():
    def handler(request):
        body = json.loads(request.content) if request.method == "POST" else {}
        if "=== ВЪЗЗИВНО РЕШЕНИЕ ===" in str(body):
            out = {"status": "completed", "usage": {}, "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(ANALYSIS)}]}]}
            return httpx.Response(200, json={"id": "a", **out})
        return httpx.Response(429, text='{"error": {"code": "insufficient_quota"}}')

    ai = OpenAIProvider(AIConfig("strong", "light", max_calls=40, workers=1),
                        transport=httpx.MockTransport(handler), sleep=lambda s: None)
    doc = SourceDoc("С", "file:///s", APPELLATE, "txt", "now")
    with pytest.raises(AIQuotaError):
        run_analysis(ai, FakeVKS().client(), doc, date(2022, 1, 1), overlap=True, local_first=False)


# ---------- local-first ----------

def test_local_first_skips_only_questions_with_enough_art_290_decisions():
    local = [
        ("В1", [["волята", "платеца"]], {"d1": {"proceeding_article": "290"},
                                          "d2": {"proceeding_article": "290"}}),
        ("В1", [["избор", "платеца"]], {"d2": {"proceeding_article": "290"},   # same decision again
                                         "d3": {"proceeding_article": "290"},
                                         "tr": {"proceeding_article": "ТР"}}),
        ("В2", [["давност"]], {"d4": {"proceeding_article": "290"}, "tr": {"proceeding_article": "ТР"},
                               "d5": {"proceeding_article": "290"}}),
    ]
    assert pipeline.LOCAL_FIRST_MIN == pipeline.PER_QUESTION == 3
    assert local_first_skips(local) == {"В1": 3}          # В2: 2 decisions + a TR is not enough
    assert local_first_skips(local, threshold=2) == {"В1": 3, "В2": 2}
    assert local_first_skips([]) == {}


def test_search_vks_records_skipped_live_searches():
    fake = FakeVKS()
    log: list[dict] = []
    found = search_vks(fake.client(), ANALYSIS["search"], date(2022, 1, 1), log, skip_live={"В1": 4})

    words_sent = [parse_qs(urlparse(u).query)["AktDumiVSadarjanie"][0] for u in fake.urls]
    assert words_sent == ["давност вземане", "давност платеца"]   # nothing for В1
    skipped = [s for s in log if s.get("live_skipped")]
    assert [s["words"] for s in skipped] == [["волята", "платеца"], ["избор", "платеца"]]
    assert all(s["question_id"] == "В1" and s["local_decisions"] == 4 and s["threshold"] == 3
               and "собствената база даде 4 решения по чл. 290" in s["skipped"] for s in skipped)
    assert not any("В1" in e["by_question"] for e in found.values())

    # without skips the same plan sends all four searches, as before
    fake2, log2 = FakeVKS(), []
    search_vks(fake2.client(), ANALYSIS["search"], date(2022, 1, 1), log2)
    assert len(fake2.urls) == 4 and not any(s.get("live_skipped") for s in log2)


def test_local_first_is_off_by_default(monkeypatch):
    monkeypatch.delenv("ANALYSIS_LOCAL_FIRST", raising=False)
    monkeypatch.delenv("ANALYSIS_OVERLAP", raising=False)
    assert pipeline.pipeline_options() == {"local_first": False, "overlap": True}
    monkeypatch.setenv("ANALYSIS_LOCAL_FIRST", "1")
    monkeypatch.setenv("ANALYSIS_OVERLAP", "0")
    assert pipeline.pipeline_options() == {"local_first": True, "overlap": False}


# ---------- polling ----------

def _poll_provider(n_in_progress: int, **cfg):
    sleeps: list[float] = []
    state = {"left": n_in_progress}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "r", "status": "queued"})
        if state["left"] > 0:
            state["left"] -= 1
            return httpx.Response(200, json={"id": "r", "status": "in_progress"})
        return httpx.Response(200, json={"id": "r", "status": "completed", "usage": {}, "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]})

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=5, **cfg), transport=httpx.MockTransport(handler),
                        sleep=sleeps.append)
    return ai, sleeps


def _call(ai):
    return ai.structured(model="m", system="s", user="u", schema_name="x", schema={"type": "object"})


def test_poll_backoff_schedule():
    assert POLL_BACKOFF == (0.5, 1.0, 1.5, 2.0)
    ai, sleeps = _poll_provider(7)
    assert _call(ai) == {}
    assert sleeps == [0.5, 1.0, 1.5, 2.0, 3.0, 3.0, 3.0, 3.0]  # queued + 7 x in_progress

    ai, sleeps = _poll_provider(3, poll_backoff=False)
    _call(ai)
    assert sleeps == [3.0, 3.0, 3.0, 3.0]  # the old fixed interval


def test_poll_keeps_the_max_wait():
    sleeps: list[float] = []

    def handler(request):
        return httpx.Response(200, json={"id": "r", "status": "in_progress" if request.method == "GET"
                                         else "queued"})

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=5), transport=httpx.MockTransport(handler),
                        sleep=sleeps.append, max_wait=900.0)
    with pytest.raises(AIError, match="900 сек"):
        _call(ai)
    assert sum(sleeps) >= 900.0 and sum(sleeps) - sleeps[-1] < 900.0


def test_direct_mode_sends_no_background_flag_and_waits_up_to_max_wait():
    seen = []

    def handler(request):
        seen.append((request.method, json.loads(request.content) if request.content else None,
                     request.extensions.get("timeout")))
        return httpx.Response(200, json={"id": "r", "status": "completed", "usage": {}, "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}]})

    sleeps: list[float] = []
    ai = OpenAIProvider(AIConfig("m", "m", max_calls=5, background=False),
                        transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert _call(ai) == {"ok": True}
    assert [m for m, _, _ in seen] == ["POST"] and "background" not in seen[0][1]
    assert seen[0][2]["read"] == 900.0 and sleeps == []


def test_direct_mode_timeout_and_quota_errors():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=5, background=False),
                        transport=httpx.MockTransport(slow), sleep=lambda s: None)
    with pytest.raises(AIError, match="не приключи"):
        _call(ai)

    def no_credit(request):
        return httpx.Response(429, text='{"error": {"code": "insufficient_quota"}}')

    ai = OpenAIProvider(AIConfig("m", "m", max_calls=5, background=False),
                        transport=httpx.MockTransport(no_credit), sleep=lambda s: None)
    with pytest.raises(AIQuotaError):
        _call(ai)


def test_runtime_env(monkeypatch):
    for var in ("AI_BACKGROUND", "AI_POLL_BACKOFF", "AI_WORKERS"):
        monkeypatch.delenv(var, raising=False)
    assert runtime_env() == {"background": True, "poll_backoff": True, "workers": 4}
    monkeypatch.setenv("AI_BACKGROUND", "0")
    monkeypatch.setenv("AI_POLL_BACKOFF", "0")
    monkeypatch.setenv("AI_WORKERS", "8")
    assert runtime_env() == {"background": False, "poll_backoff": False, "workers": 8}
    monkeypatch.setenv("AI_WORKERS", "50")
    assert runtime_env()["workers"] == 12
    monkeypatch.setenv("AI_WORKERS", "0")
    assert runtime_env()["workers"] == 1
    monkeypatch.setenv("AI_WORKERS", "много")
    with pytest.raises(AIError, match="AI_WORKERS"):
        runtime_env()
    monkeypatch.setenv("AI_WORKERS", "4")
    monkeypatch.setenv("AI_BACKGROUND", "може би")
    with pytest.raises(AIError, match="AI_BACKGROUND"):
        runtime_env()


# ---------- call cap under concurrency ----------

def _counting_transport(delay=0.005, bad_json=False):
    lock = threading.Lock()
    posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"id": "r", "status": "completed", "usage": {"input_tokens": 1},
                                             "output": [{"type": "message", "content": [
                                                 {"type": "output_text", "text": "{"}]}]})
        time.sleep(delay)  # keep many requests in flight at once
        with lock:
            posts.append(1)
        return httpx.Response(200, json={"id": "r", "status": "completed", "usage": {"input_tokens": 1},
                                         "output": [{"type": "message", "content": [
                                             {"type": "output_text", "text": "{" if bad_json else "{}"}]}]})
    return httpx.MockTransport(handler), posts


@pytest.mark.parametrize("cap", [0, 1, 7, 36])
def test_call_cap_is_exact_under_concurrency(cap):
    from concurrent.futures import ThreadPoolExecutor

    transport, posts = _counting_transport()
    ai = OpenAIProvider(AIConfig("m", "m", max_calls=cap), transport=transport, sleep=lambda s: None)
    barrier = threading.Barrier(12)

    def worker(_):
        barrier.wait()
        ok = err = 0
        for _ in range(3):
            try:
                _call(ai)
                ok += 1
            except AIError:
                err += 1
        return ok, err

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(worker, range(12)))
    ok = sum(r[0] for r in results)
    assert len(posts) == ok == ai.usage.calls == ai.calls_started == min(cap, 36)
    assert sum(r[1] for r in results) == 36 - min(cap, 36)


def test_call_cap_counts_retries():
    transport, posts = _counting_transport(delay=0, bad_json=True)
    ai = OpenAIProvider(AIConfig("m", "m", max_calls=3), transport=transport, sleep=lambda s: None)
    with pytest.raises(AIError, match="не е валиден след 2 опита"):
        _call(ai)   # two attempts
    with pytest.raises(AIError, match="лимит от 3"):
        _call(ai)   # one attempt left, the retry is refused
    assert len(posts) == 3 == ai.calls_started
