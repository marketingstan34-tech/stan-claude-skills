"""Draft statement of cassation grounds (SYNTHETIC report)."""

import io
import zipfile

from legal_ai.cassation.draft import build_draft, to_docx, to_text

RUN = {
    "appellate": {"label": "Апелативен съд Пловдив, Въззивно търговско дело № 1/2020, Решение от 02.03.2021",
                  "url": "https://example.invalid/x"},
    "analysis": {
        "case_summary": "Синтетичен казус.",
        "lower_instance": {"act": "", "date": "", "case": "", "court": ""},
        "holdings": [{"id": "H1", "summary": "Въззивният съд е приел, че Синтетичният извод е верен.", "quote": "x"}],
        "questions": [
            {"id": "Q1", "text": "Синтетичен въпрос едно?", "kind": "материалноправен", "holding_ids": ["H1"],
             "ground": "т.1", "why_decisive": "Защото."},
            {"id": "Q2", "text": "Синтетичен въпрос две?", "kind": "процесуалноправен", "holding_ids": [],
             "ground": "т.3", "why_decisive": ""},
        ],
        "search": []},
    "holding_quotes": {"H1": {"text": "дословен извод", "status": "text_verified"}},
    "assessments": [
        {"question_id": "Q1", "label": "Решение №1/01.01.2015 по дело №1/2014", "chamber": "Първо търговско отделение",
         "relevant": True, "stance": "противоречи", "vks_rule": "Правилото е друго.",
         "quote": {"text": "проверен цитат", "status": "text_verified"}},
        {"question_id": "Q1", "label": "Решение №2/02.02.2016 по дело №2/2015", "chamber": None,
         "relevant": True, "stance": "противоречи", "vks_rule": "Друго правило.",
         "quote": {"text": "НЕПРОВЕРЕН", "status": "not_found"}},
        {"question_id": "Q1", "label": "Решение №3/03.03.2017 по дело №3/2016", "chamber": None,
         "relevant": True, "stance": "подкрепя", "vks_rule": "Подкрепящо.", "quote": {"text": "", "status": "not_found"}},
    ],
}


def test_draft_uses_only_verified_quotes_and_contra_practice():
    text = to_text(build_draft(RUN))
    assert "ДО ВЪРХОВНИЯ КАСАЦИОНЕН СЪД" in text and "ЧРЕЗ АПЕЛАТИВЕН СЪД ПЛОВДИВ" in text
    assert "По чл. 280, ал. 1, т. 1 и чл. 280, ал. 1, т. 3 от ГПК" in text
    assert "На първо място, въззивният съд е приел, че синтетичният извод е верен." in text
    assert "„дословен извод“" in text and "„проверен цитат“" in text
    assert "НЕПРОВЕРЕН" not in text and "не е проверен дословно" in text
    assert "Решение №3/03.03.2017" not in text            # supporting practice is not cited
    assert "1. „Синтетичен въпрос едно?“" in text and "2. „Синтетичен въпрос две?“" in text
    assert "чл. 280, ал. 1, т. 3 ГПК: обосновете" in text
    assert "Прилагам:" in text and "1. Решение №1/01.01.2015 по дело №1/2014;" in text


def test_docx_is_a_valid_word_file():
    data = to_docx(build_draft(RUN))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert {"[Content_Types].xml", "word/document.xml", "word/styles.xml"} <= set(z.namelist())
        xml = z.read("word/document.xml").decode()
    assert "ИЗЛОЖЕНИЕ НА КАСАЦИОННИ ОСНОВАНИЯ" in xml and "Times New Roman" not in xml
