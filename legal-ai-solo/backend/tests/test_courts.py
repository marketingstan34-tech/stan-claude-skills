"""SYNTHETIC fixtures shaped like the justice.bg acts table and ecase.justice.bg files."""

from datetime import date

import httpx
import pytest

from legal_ai.http import FetchError, PoliteClient, host_allowed
from legal_ai.sources.courts import COURTS
from legal_ai.sources.courts.acts import acts_url, parse_acts
from legal_ai.sources.courts.document import _reflow_layout, extract_text, html_charset

ACTS_PAGE = """<html><body><table class="results-table">
<tr class="table-titles"><th>№</th><th>Вид дело</th><th>Номер / година</th><th>Ищец</th>
<th>Ответник</th><th>Съдия - докладчик</th><th>Вид акт</th><th>Дата</th><th>Файл</th><th>Мотиви</th></tr>
<tr class="table-data"><td>1</td><td>Въззивно гражданско дело</td><td>12 / 2020</td>
<td>ИМЕ НА СТРАНА</td><td></td><td>Съдия Синтетичен</td><td>Решение</td><td>05.03.2021</td>
<td><a href="https://ecase.justice.bg/act/getactpublicfile?guid=00000000-1111-2222-3333-444444444444">Свали</a></td><td></td></tr>
<tr class="table-data"><td>2</td><td>Въззивно гражданско дело</td><td>12 / 2020</td><td></td><td></td>
<td>Съдия Синтетичен</td><td>Определение</td><td>01.02.2021</td><td></td><td></td></tr>
<tr class="table-data"><td>3</td><td>Въззивно гражданско дело</td><td>12 / 2020</td><td></td><td></td>
<td>Съдия Синтетичен</td><td>Решение</td><td>01.02.2021</td>
<td><a href="https://evil.example/act?guid=00000000-1111-2222-3333-444444444444">Свали</a></td><td></td></tr>
</table></body></html>"""


def test_acts_url_uses_the_court_page_and_case_params():
    url = acts_url(COURTS["os-plovdiv"], 958, 2020, "Гражданско")
    assert url.startswith("https://plovdiv-os.justice.bg/bg/3935?")
    assert "casenumber=958" in url and "caseyear=2020" in url and "actkindcode=5001" in url
    with pytest.raises(ValueError):
        acts_url(COURTS["os-plovdiv"], 1, 2020, "Наказателно")


def test_parse_acts_rows_link_only_to_ecase_and_no_party_names():
    rows = parse_acts(ACTS_PAGE)
    assert [r.act_type for r in rows] == ["Решение", "Определение", "Решение"]
    assert rows[0].case_number == 12 and rows[0].case_year == 2020
    assert rows[0].act_date == date(2021, 3, 5)
    assert rows[0].guid == "00000000-1111-2222-3333-444444444444"
    assert rows[1].file_url is None
    assert rows[2].file_url is None  # foreign host rejected
    assert not hasattr(rows[0], "plaintiff")


def test_word_html_in_windows_1251_is_decoded_by_meta_charset():
    markup = ('<html><head><meta http-equiv=Content-Type content="text/html; charset=windows-1251">'
              '</head><body><div class=WordSection1><p class=MsoNormal>Р Е Ш Е Н И Е</p>'
              '<p class=MsoNormal>СИНТЕТИЧЕН\r\nтекст на\r\n абзац.</p></div></body></html>')
    body = markup.encode("cp1251")
    assert html_charset(body) == "windows-1251"
    t = extract_text(body, "text/html")  # no charset in the header, like ecase
    assert t.fmt == "html" and t.encoding == "windows-1251"
    assert t.text == "Р Е Ш Е Н И Е\nСИНТЕТИЧЕН текст на абзац."
    assert t.warnings == []


def test_wrong_header_charset_falls_back_and_is_flagged():
    body = '<html><body><p>Текст</p></body></html>'.encode("cp1251")
    t = extract_text(body, "text/html; charset=utf-8")  # wrong header on purpose
    assert t.text == "Текст" and t.encoding == "windows-1251"
    assert any("header" in w for w in t.warnings)


def test_layout_reflow_joins_wrapped_lines_and_splits_on_indent():
    page = ("                 РЕШЕНИЕ\n\n"
            "            Постъпила е жалба   от   страна\n"
            "против решение по делото.\n"
            "            Съдът намира следното:\n"
            "текстът продължава тук.\n")
    assert _reflow_layout(page) == [
        "РЕШЕНИЕ",
        "Постъпила е жалба от страна против решение по делото.",
        "Съдът намира следното: текстът продължава тук.",
    ]


def test_unknown_format_is_rejected():
    from legal_ai.sources.courts.document import DocumentError
    with pytest.raises(DocumentError):
        extract_text(b"\x89PNG....")


def test_host_allowlist_patterns():
    assert host_allowed("plovdiv-os.justice.bg", ["*.justice.bg"])
    assert not host_allowed("justice.bg", ["*.justice.bg"])
    assert not host_allowed("evil.justice.bg.example", ["*.justice.bg"])
    assert host_allowed("www.vks.bg", ["www.vks.bg"])


def test_polite_client_refuses_other_hosts_and_http():
    client = PoliteClient(["ecase.justice.bg"], transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                          sleep=lambda s: None)
    with pytest.raises(FetchError):
        client.get("https://example.com/x")
    with pytest.raises(FetchError):
        client.get("http://ecase.justice.bg/x")
    assert client.get("https://ecase.justice.bg/x").status == 200
