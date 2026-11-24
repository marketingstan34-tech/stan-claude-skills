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
        read_upload("photo.heic", b"x" * 1000)
    with pytest.raises(UploadError, match="почти няма текст"):
        read_upload("short.txt", "кратко".encode())
    with pytest.raises(UploadError, match="Word"):
        read_upload("broken.docx", b"not a zip" * 100)


def _photo(size=(400, 300), orientation=None):
    from PIL import Image
    im, buf = Image.new("RGB", size, "white"), io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_photo_of_a_page(monkeypatch, tmp_path):
    """JPEG/PNG: checked as a real image when the form is sent, read with OCR in the job (cached),
    turned upright by its EXIF orientation; a power of attorney or an unreadable photo is refused."""
    import legal_ai.upload as up
    monkeypatch.setattr(up, "ocr_available", lambda: True)
    seen = []

    def fake_tesseract(cmd, **kw):
        from PIL import Image
        with Image.open(cmd[1]) as im:
            seen.append((im.mode, im.size))
        return type("R", (), {"stdout": ("Решение Хе 125 " + "текст на решението " * 10).encode()})()
    monkeypatch.setattr(up.subprocess, "run", fake_tesseract)
    photo = _photo(orientation=6)   # stored on its side, as phones do
    assert read_upload("IMG_0001.JPG", photo, ocr=False) == ("", "image-scan", [up.OCR_NOTE])
    assert not seen   # no OCR when the form is sent
    text, fmt, warnings = read_upload("IMG_0001.JPG", photo)
    assert fmt == "image-ocr" and text.startswith("Решение № 125") and warnings == [up.OCR_NOTE]
    assert seen == [("L", (1350, 1800))]   # grey, upright (portrait) and enlarged
    stored = tmp_path / "a.jpg"
    stored.write_bytes(photo)
    assert up.read_stored(stored, "IMG_0001.JPG")[1] == "image-ocr" and len(seen) == 2
    assert up.read_stored(stored, "IMG_0001.JPG")[0] == text and len(seen) == 2   # read once, then cached
    with pytest.raises(UploadError, match="не може да се отвори"):
        read_upload("scan.png", b"x" * 1000, ocr=False)
    monkeypatch.setattr(up.subprocess, "run", lambda cmd, **kw: type("R", (), {"stdout": "ПЪЛНОМОЩНО\nупълномощавам".encode()})())
    with pytest.raises(UploadError, match="пълномощно"):
        read_upload("p.jpg", photo)
    monkeypatch.setattr(up.subprocess, "run", lambda cmd, **kw: type("R", (), {"stdout": b"~~ ."})())
    with pytest.raises(UploadError, match="почти няма четим текст"):
        read_upload("blur.jpg", photo)
    monkeypatch.setattr(up, "ocr_available", lambda: False)
    with pytest.raises(UploadError, match="Качете PDF или Word"):
        read_upload("p.jpg", photo, ocr=False)
