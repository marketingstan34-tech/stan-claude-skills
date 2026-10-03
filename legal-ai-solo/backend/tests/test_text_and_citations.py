from legal_ai.citations.verify import TextStatus, text_hash, verify_quote
from legal_ai.retrieval.text import Term, build_tsquery, normalize_for_search, parse_query, stem


def test_normalize_splits_references_and_lowercases():
    assert normalize_for_search("Чл.349, ал.2 ГПК") == "чл 349 ал 2 гпк"


def test_stem_strips_one_common_ending_and_keeps_short_words():
    assert stem("делбата") == "делб"
    assert stem("имоти") == "имот"
    assert stem("имот") == "имот"
    assert stem("гпк") == "гпк"


def test_parse_query_builds_reference_phrases_and_drops_stopwords():
    terms = parse_query("възлагане на имота при делба по чл. 349 ГПК")
    assert Term("phrase", "чл 349") in terms
    assert Term("word", "имот") in terms
    assert Term("word", "делб") in terms
    assert all(t.value not in {"на", "при", "по"} for t in terms)
    tsq = build_tsquery(terms)
    assert "(чл <-> 349)" in tsq and "делб:*" in tsq and " & " in tsq
    assert " | " in build_tsquery(terms, "any")


def test_verify_quote_statuses():
    text = "Първи абзац.\nВторият   абзац съдържа извода."
    start = text.index("Вторият")
    end = len(text)
    assert verify_quote(text, start, end, "Вторият абзац съдържа извода.") is TextStatus.TEXT_VERIFIED
    assert verify_quote(text, start, end, "Вторият абзац съдържа друго.") is TextStatus.INVALID
    assert verify_quote(text, 0, len(text) + 1, "x") is TextStatus.INVALID
    assert verify_quote(None, 0, 1, "x") is TextStatus.SOURCE_UNAVAILABLE
    assert verify_quote(text, start, end, text[start:end], expected_hash=text_hash(text)) \
        is TextStatus.TEXT_VERIFIED
    assert verify_quote(text, start, end, text[start:end], expected_hash="0" * 64) \
        is TextStatus.INVALID
