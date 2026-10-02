"""SYNTHETIC: enumeration of interpretative decisions stops after misses and checks PDF magic."""

import httpx

from legal_ai.http import PoliteClient
from legal_ai.sources.vks.interpretive import download_all, find_date, tr_url


def test_tr_url_pattern():
    assert tr_url("osgtk", 2013, 1) == ("https://www.vks.bg/talkuvatelni-dela-osgtk/"
                                        "vks-osgtk-tdelo-2013-1-reshenie.pdf")


def test_find_date_in_heading():
    from datetime import date
    assert find_date("ТЪЛКУВАТЕЛНО РЕШЕНИЕ № 1/2013 гр. София, 9 декември 2013 год.") == date(2013, 12, 9)


def test_download_stops_after_gap_and_rejects_non_pdf(tmp_path):
    existing = {tr_url("osgtk", 2013, 1), tr_url("osgtk", 2013, 2), tr_url("osgk", 2013, 1)}

    def handler(request):
        url = str(request.url)
        if url == tr_url("osgk", 2013, 1):
            return httpx.Response(200, content=b"<html>not a pdf</html>")
        if url in existing:
            return httpx.Response(200, content=b"%PDF-1.4 synthetic")
        return httpx.Response(404)

    seen = []

    def spy(request):
        seen.append(str(request.url))
        return handler(request)

    client = PoliteClient(["www.vks.bg"], transport=httpx.MockTransport(spy), sleep=lambda s: None)
    files, log = download_all(client, tmp_path, range(2013, 2014), gap=2)
    assert [(f.college, f.number) for f in files] == [("osgtk", 1), ("osgtk", 2)]
    assert any("не е PDF" in line for line in log)
    # osgtk: 1, 2 ok, 3 and 4 missing -> stop; osgk: 1 not pdf, 2 missing -> stop; ostk: 1, 2 missing
    assert len([u for u in seen if "osgtk" in u]) == 4
    assert len([u for u in seen if "/talkuvatelni-dela-osgk/" in u]) == 2
