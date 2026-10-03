"""Uploaded documents (SYNTHETIC content)."""

import io
import zipfile
from datetime import date

import pytest

from legal_ai.upload import UploadError, first_date, read_upload

BODY = ("Р Е Ш Е Н И Е № 125 гр. Пловдив, 14.03.2022 г. Синтетичен текст на решение. "
        "Образувано е по въззивна жалба против Решение № 260311 от 06.07.2021г., постановено по "
        "т.д. №1110/2019г. по описа на О.С.-П. ") * 4


def _docx(paragraphs):
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    xml = f'<w:document {ns}><w:body>' + "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs) + "</w:body></w:document>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_docx_and_txt():
    text, fmt, _ = read_upload("reshenie.docx", _docx(["Първи абзац.", BODY]))
    assert fmt == "docx" and text.startswith("Първи абзац.") and "260311" in text
    text, fmt, _ = read_upload("r.txt", BODY.encode("windows-1251"))
    assert fmt == "txt" and "Пловдив" in text
    assert first_date(text) == date(2022, 3, 14)


def test_rejected_files():
    with pytest.raises(UploadError, match="docx или PDF"):
        read_upload("old.doc", b"x" * 1000)
    with pytest.raises(UploadError, match="Позволени"):
        read_upload("photo.jpg", b"x" * 1000)
    with pytest.raises(UploadError, match="почти няма текст"):
        read_upload("short.txt", "кратко".encode())
    with pytest.raises(UploadError, match="Word"):
        read_upload("broken.docx", b"not a zip" * 100)
