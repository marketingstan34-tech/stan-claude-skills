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
        # forms seen live 02.10.2026 (Cyrillic І as a Roman numeral, abbreviations)
        "ВЪРХОВНИЯТ КАСАЦИОНЕН СЪД на Република България, Търговска колегия, \u0406 отделение,":
            "Първо търговско отделение",
        "ВЪРХОВЕН КАСАЦИОНЕН СЪД на Република България, ТК, II отделение , в открито заседание":
            "Второ търговско отделение",
        "Върховният касационен съд на Република България, трето гр. отделение, в публичното":
            "Трето гражданско отделение",
        "Върховният касационен съд на Република България, I\u0406\u0406 гражданско отделение в":
            "Трето гражданско отделение",
        "ВЪРХОВНИЯТ КАСАЦИОНЕН СЪД, ГК,\u0406\u0406\u0406 г.о.в открито заседание": "Трето гражданско отделение",
        "ВЪРХОВЕН КАСАЦИОНЕН СЪД , ТК , I т.о., в публичното заседание": "Първо търговско отделение",
        "ВЪРХОВНИЯТ КАСАЦИОНЕН СЪД, ГК ,Трето г.о.,в открито заседание": "Трето гражданско отделение",
        "ВЪРХОВЕН КАСАЦИОНЕН СЪД, ГК, \u0406V отд., на деветнадесети април": "Четвърто гражданско отделение",
        "ВЪРХОВЕН КАСАЦИОНЕН СЪД – Търговска колегия, състав на \u0406 т.о. в публичното":
            "Първо търговско отделение",
    }
    for head, expected in cases.items():
        act = _act(head)
        assert act.chamber == expected, head
        assert act.proceeding_article == "290"


def test_chamber_not_guessed_without_college():
    act = _act("Върховният касационен съд, Второ отделение, в открито заседание")
    assert act.chamber is None and "chamber_not_found" in act.warnings


def test_extract_280_grounds_variants():
    from legal_ai.sources.vks.parser import extract_280_grounds
    assert extract_280_grounds(
        "в приложното поле на чл. 280, ал. 1, т. 1 и ал. 2, пр. 3 ГПК") == [
        "чл. 280, ал. 1, т. 1", "чл. 280, ал. 2, предл. 3"]
    assert extract_280_grounds("на основание чл.280, ал.2, предл.2 ГПК") == ["чл. 280, ал. 2, предл. 2"]
    assert extract_280_grounds("поради вероятна недопустимост на въззивното решение") == [
        "чл. 280, ал. 2, предл. 2"]
    assert extract_280_grounds("по чл. 280, ал. 1, т. 3 ГПК, а по чл. 290, ал. 2 ГПК") == [
        "чл. 280, ал. 1, т. 3"]


def test_admission_in_reverse_word_order_and_grounds_in_next_paragraph():
    html = ('<div id="Content">Р Е Ш Е Н И Е<br>Второ гражданско отделение<br>'
            'Производството е по чл. 290 ГПК.<br>'
            'Касационното обжалване е допуснато с определение № 1.<br>'
            'Допускането е на основание чл. 280, ал. 1, т. 1 ГПК по въпроса за делбата.<br>'
            'Р Е Ш И:<br>ОСТАВЯ В СИЛА.</div>')
    act = parse_act(html)
    assert len(act.admission_paragraph_nos) == 1
    assert act.admission_grounds == ["чл. 280, ал. 1, т. 1"]


def test_extract_280_grounds_proposal_words():
    from legal_ai.sources.vks.parser import extract_280_grounds
    assert extract_280_grounds("на основание чл.280, ал.2, предл.последно ГПК") == ["чл. 280, ал. 2, предл. 3"]
    assert extract_280_grounds("по чл. 280, ал. 2, предл. второ ГПК") == ["чл. 280, ал. 2, предл. 2"]
    assert extract_280_grounds("по чл. 280, ал. 2 ГПК") == ["чл. 280, ал. 2"]


def test_parse_list_accepts_single_quoted_links_as_served_by_the_live_site():
    # SYNTHETIC, shaped like the direct (non-Firecrawl) response.
    html = ("<div id='TablicaRezultati'><div><div><a href='pregled-akt.jsp?type=ot-spisak&id="
            + "B" * 32 + "'>Решение №5/02.03.2016 по дело №7/2015</a></div><div>анотация</div></div></div>")
    rows = parse_list(html)
    assert len(rows) == 1 and rows[0].source_id == "B" * 32 and rows[0].act_number == "5"


def test_proceeding_article_with_po_deloto_phrasing():
    from legal_ai.sources.vks.parser import _PROCEEDING
    m = _PROCEEDING.search("Производството по делото е по реда на чл. 290 ГПК в редакцията на текста")
    assert m and m.group(1) == "290"
    assert _PROCEEDING.search("Производството е образувано по чл. 290 ГПК").group(1) == "290"


def test_list_link_text_rare_forms():
    from legal_ai.sources.vks.parser import parse_list
    texts = {
        "Решение №60 189/14.10.2021 по дело №1299/2021": ("60189", "14.10.2021"),
        "Решение №60160А/13.12.2021 по дело №174/2021": ("60160А", "13.12.2021"),
        "Решение №160-A/16.12.2020 по дело №2156/2019": ("160-A", "16.12.2020"),
        "Решение №214/18/08.01.2019 по дело №3921/2017": ("214", "08.01.2019"),
        "Решение №265/2018/07.01.2019 по дело №2719/2018": ("265", "07.01.2019"),
        "Решение №60249/2021 г./20.05.2022 по дело №4040/2020": ("60249", "20.05.2022"),
        "Решение №/29.09.2020 по дело №2479/2019": (None, "29.09.2020"),
        "Решение №90/14.04.2022 по дело №2990/2021": ("90", "14.04.2022"),
    }
    html = "".join(f"<a href='pregled-akt.jsp?type=ot-spisak&amp;id={i:032X}'>{t}</a>"
                   for i, t in enumerate(texts))
    rows = parse_list(html)
    got = {r.link_text: (r.act_number, r.act_date.strftime("%d.%m.%Y") if r.act_date else None)
           for r in rows}
    assert got == texts
    assert all(r.case_number and r.case_year for r in rows)
