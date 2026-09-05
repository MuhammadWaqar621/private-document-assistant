"""Unit tests for engine/extraction.py, using real fixture files
(tests/fixtures/sample.pdf, sample.txt) rather than mocking pdfplumber/
python-docx - the point of this module is turning real file bytes into
text, so it's tested against real files."""

import pytest

from app.engine.extraction import UnsupportedFileTypeError, extract_pages

from .conftest import FIXTURES_DIR


def _read(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_extract_pdf_returns_one_entry_per_page():
    pages = extract_pages(_read("sample.pdf"), "sample.pdf")

    assert len(pages) == 2
    page_numbers = [page_number for page_number, _ in pages]
    assert page_numbers == [1, 2]


def test_extract_pdf_text_content_per_page():
    pages = extract_pages(_read("sample.pdf"), "sample.pdf")
    page_1_text = pages[0][1]
    page_2_text = pages[1][1]

    assert "Page One" in page_1_text
    assert "first page" in page_1_text
    assert "Page Two" in page_2_text
    assert "second page" in page_2_text
    # Page 1's distinguishing content must not leak into page 2's text.
    assert "Page One" not in page_2_text


def test_extract_txt_is_treated_as_a_single_page():
    pages = extract_pages(_read("sample.txt"), "sample.txt")

    assert len(pages) == 1
    page_number, text = pages[0]
    assert page_number == 1
    assert "QueryNest Test Document (TXT)" in text
    assert "no notion of pages" in text


def test_extract_txt_decodes_utf8_with_replacement_on_bad_bytes():
    # Invalid UTF-8 byte sequence - _extract_txt must not raise, it decodes
    # with errors="replace" instead.
    raw = b"valid start \xff\xfe invalid bytes end"
    pages = extract_pages(raw, "broken.txt")

    assert len(pages) == 1
    assert pages[0][0] == 1
    assert "valid start" in pages[0][1]
    assert "end" in pages[0][1]


def test_unsupported_extension_raises():
    with pytest.raises(UnsupportedFileTypeError):
        extract_pages(b"whatever bytes", "malware.exe")


def test_unsupported_extension_error_message_names_the_extension():
    with pytest.raises(UnsupportedFileTypeError, match=r"\.xyz"):
        extract_pages(b"data", "notes.xyz")


def test_no_extension_at_all_raises_unsupported():
    with pytest.raises(UnsupportedFileTypeError):
        extract_pages(b"data", "no_extension_at_all")
