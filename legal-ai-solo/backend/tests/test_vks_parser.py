from datetime import date
from pathlib import Path

from legal_ai.sources.vks.parser import parse_act, parse_list
from legal_ai.sources.vks.urls import ListQuery, act_url, list_url

RAW = Path(__file__).parent / "fixtures" / "synthetic_raw"
ID1 = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"


def test_parse_list_dedupes_and_parses_link_text():
    rows = parse_list((RAW / "lists" / "2025-01.html").read_text(encoding="utf-8"))
    assert [r.source_id for r in rows] == [ID1, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA2"]
    r = rows[0]
    assert (r.act_type, r.act_number, r.act_date, r.case_number, r.case_year) == (
        "решение", "901", date(2025, 1, 15), "4001", 2024)


def test_parse_list_keeps_unparseable_rows_with_raw_text():
    html = '<a href="pregled-akt.jsp?type=ot-spisak&id=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB1">странен текст</a>'
    (row,) = parse_list(html)
    assert row.link_text == "странен текст" and row.act_number is None


def test_parse_act_metadata_sections_and_offsets():
    act = parse_act((RAW / "acts" / f"{ID1}.html").read_text(encoding="utf-8"))
    assert act.heading == "РЕШЕНИЕ"
    assert act.chamber == "Второ гражданско отделение"
    assert act.proceeding_article == "290"
    assert act.admission_grounds == ["чл. 280, ал. 1, т. 1"]
    assert act.warnings == []
    for p in act.paragraphs:
        assert act.canonical_text[p.start:p.end] == p.text
    sections = {p.text: p.section for p in act.paragraphs}
    assert sections["Р Е Ш И:"] == "dispositive"
    assert sections["Решението е окончателно."] == "dispositive"
    assert sections["Производството е по чл.290 ГПК."] == "reasoning"
    admission = [act.paragraphs[i].text for i in act.admission_paragraph_nos]
    assert len(admission) == 1 and "допуснато касационно обжалване" in admission[0]


def test_parse_act_normalizes_whitespace_and_nbsp():
    act = parse_act((RAW / "acts" / f"{ID1}.html").read_text(encoding="utf-8"))
    line = next(p.text for p in act.paragraphs if p.text.startswith("По поставения въпрос"))
    assert "а имотът трябва" in line and "\xa0" not in line and "  " not in line


def test_parse_act_without_content_div_warns():
    act = parse_act((RAW / "acts" / "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA2.html").read_text(encoding="utf-8"))
    assert act.canonical_text == "" and act.warnings == ["content_div_missing"]


def test_list_url_matches_verified_form_encoding():
    url = list_url(ListQuery(2025, 1, 2025, 3, words="делба"))
    assert url.startswith("https://www.vks.bg/spisak-aktove.jsp?")
    assert "AktVidDelo=%D0%B3%D1%80." in url
    assert "AktOtdelenie=empty" in url
    assert "AktNoOtMesec=01" in url and "AktNoDoMesec=03" in url
    assert "AktDumiVSadarjanie=%D0%B4%D0%B5%D0%BB%D0%B1%D0%B0" in url
    assert act_url(ID1) == f"https://www.vks.bg/pregled-akt.jsp?type=ot-spisak&id={ID1}"


def _act(head_line: str, proceeding_line: str = "Производството е по реда на чл.290 и сл. ГПК."):
    html = (f'<div id="Content">Р Е Ш Е Н И Е<br>{head_line}<br>{proceeding_line}<br>'
            'Р Е Ш И:<br>ОСТАВЯ В СИЛА.</div>')
    return parse_act(html)


def test_chamber_variants_and_proceeding_por_reda():
    cases = {
        "Върховният касационен съд, Гражданска колегия, Първо отделение, в открито заседание":
            "Първо гражданско отделение",
        "Върховният касационен съд, първо отделение на Гражданска колегия в открито заседание":
            "Първо гражданско отделение",
        "Върховният касационен съд, гражданска колегия, I-во отделение, в открито заседание":
            "Първо гражданско отделение",
        "Върховният касационен съд, Търговска колегия, Второ отделение":
            "Второ търговско отделение",
    }
    for head, expected in cases.items():
        act = _act(head)
        assert act.chamber == expected, head
        assert act.proceeding_article == "290"


def test_chamber_not_guessed_without_college():
    act = _act("Върховният касационен съд, Второ отделение, в открито заседание")
    assert act.chamber is None and "chamber_not_found" in act.warnings
