from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from legal_ai.sources.vks.crawler import FetchError, VksClient, crawl, months_between


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def _list_html(ids):
    return "".join(
        f'<a href="https://www.vks.bg/pregled-akt.jsp?type=ot-spisak&amp;id={i}">'
        f"Решение №{n}/15.01.2025 по дело №{n}/2024</a>"
        for n, i in enumerate(ids, 1)
    )


def _client(handler, clock=None):
    clock = clock or FakeClock()
    return VksClient(2.0, transport=httpx.MockTransport(handler), sleep=clock.sleep,
                     clock=clock.time), clock


def test_rejects_hosts_outside_allowlist():
    client, _ = _client(lambda r: httpx.Response(200, text="x"))
    with pytest.raises(FetchError):
        client.get("https://example.com/")
    with pytest.raises(FetchError):
        client.get("http://www.vks.bg/search.html")


def test_enforces_minimum_interval_between_requests():
    client, clock = _client(lambda r: httpx.Response(200, text="ok"))
    client.get("https://www.vks.bg/a")
    client.get("https://www.vks.bg/b")
    assert clock.sleeps == [2.0]


def test_retries_5xx_then_succeeds_and_does_not_retry_404():
    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/missing":
            calls["n"] += 1
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, text="ok")

    client, _ = _client(handler)
    assert client.get("https://www.vks.bg/x").body == b"ok"
    calls["n"] = 0
    with pytest.raises(FetchError):
        client.get("https://www.vks.bg/missing")
    assert calls["n"] == 1


def test_truncated_month_is_split_by_chamber_and_reported(tmp_path):
    full = [f"{i:032X}" for i in range(249)]

    def handler(request):
        q = parse_qs(urlparse(str(request.url)).query)
        if request.url.path.endswith("spisak-aktove.jsp"):
            chamber = q.get("AktOtdelenie", ["empty"])[0]
            if chamber == "empty" or chamber == "2-ро гр.":
                return httpx.Response(200, text=_list_html(full))
            return httpx.Response(200, text=_list_html([]))
        return httpx.Response(200, text='<div id="Content">текст</div>')

    client, _ = _client(handler)
    report = crawl(client, tmp_path, (2025, 1), (2025, 1), words="делба")
    names = [entry["name"] for entry in report.lists]
    assert names[0] == "2025-01" and len(names) == 1 + 13
    assert report.truncated == ["2025-01__2-ро-гр"]
    assert len(report.acts) == 249 and all(a["ok"] for a in report.acts)
    assert (tmp_path / "manifest.json").exists()


def test_months_between_crosses_year():
    assert months_between((2024, 11), (2025, 2)) == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]


def test_quarters_between_clips_to_range():
    from legal_ai.sources.vks.crawler import quarters_between
    assert quarters_between((2024, 11), (2025, 5)) == [(2024, 11, 12), (2025, 1, 3), (2025, 4, 5)]


def test_quarter_list_adds_acts_missing_from_monthly_lists(tmp_path):
    def handler(request):
        q = parse_qs(urlparse(str(request.url)).query)
        if request.url.path.endswith("spisak-aktove.jsp"):
            if q["AktNoOtMesec"] == q["AktNoDoMesec"]:
                return httpx.Response(200, text=_list_html(["A" * 32]))
            return httpx.Response(200, text=_list_html(["A" * 32, "B" * 32]))
        return httpx.Response(200, text='<div id="Content">текст</div>')

    client, _ = _client(handler)
    report = crawl(client, tmp_path, (2025, 1), (2025, 2))
    assert [entry["name"] for entry in report.lists] == ["2025-01", "2025-02", "2025-Q1-01-02"]
    assert sorted(a["id"] for a in report.acts) == ["A" * 32, "B" * 32]
