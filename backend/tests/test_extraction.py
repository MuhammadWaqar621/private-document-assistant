"""Unit tests for engine/extraction.py, using real fixture files
(tests/fixtures/sample.pdf, sample.txt) rather than mocking pdfplumber/
python-docx - the point of this module is turning real file bytes into
text, so it's tested against real files.

The OCR path (EasyOCR + pymupdf) is the exception: EasyOCR's Reader loads
real ML model weights, which is far too slow/heavy for the automated
suite, so these tests mock app.engine.extraction._ocr_image_bytes and
_render_pdf_page_to_png instead of the real EasyOCR/pymupdf calls - proving
the *branching logic* (real text -> never OCR'd; empty/near-empty text ->
OCR fallback invoked with the right arguments; image files -> OCR'd
directly) rather than OCR accuracy itself, which is verified separately by
live/manual testing against the real running stack (see README)."""

import pytest

import app.engine.extraction as extraction_module
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


# --- OCR fallback (PDF pages with no/near-empty text layer) ---------------


def test_pdf_page_with_real_text_is_never_sent_through_ocr(monkeypatch):
    # sample.pdf's two pages both have plenty of real embedded text
    # (pdfplumber extracts it directly) - OCR must never be invoked for
    # either page.
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("OCR must not run on a page with a real text layer")

    monkeypatch.setattr(extraction_module, "_ocr_image_bytes", _fail_if_called)
    monkeypatch.setattr(extraction_module, "_render_pdf_page_to_png", _fail_if_called)

    pages = extract_pages(_read("sample.pdf"), "sample.pdf")

    assert len(pages) == 2
    assert "Page One" in pages[0][1]
    assert "Page Two" in pages[1][1]


def _blank_pdf_bytes() -> bytes:
    """A minimal one-page PDF with no text at all (built with pymupdf,
    already a dependency) - pdfplumber extracts "" from it, exactly the
    "scanned page" situation _extract_pdf's OCR fallback exists for."""
    import fitz  # pymupdf

    doc = fitz.open()
    try:
        doc.new_page()
        return doc.tobytes()
    finally:
        doc.close()


def test_pdf_page_with_no_text_triggers_ocr_fallback(monkeypatch):
    render_calls = []
    ocr_calls = []

    def _fake_render(raw_bytes, page_number):
        render_calls.append((raw_bytes, page_number))
        return b"fake-png-bytes"

    def _fake_ocr(image_bytes):
        ocr_calls.append(image_bytes)
        return "text recovered via OCR"

    monkeypatch.setattr(extraction_module, "_render_pdf_page_to_png", _fake_render)
    monkeypatch.setattr(extraction_module, "_ocr_image_bytes", _fake_ocr)

    pages = extract_pages(_blank_pdf_bytes(), "scanned.pdf")

    assert len(pages) == 1
    assert pages[0] == (1, "text recovered via OCR")
    # Both the render step and the OCR step were invoked, with the page
    # rendered before being handed to OCR.
    assert len(render_calls) == 1
    assert render_calls[0][1] == 1  # 1-indexed page number
    assert ocr_calls == [b"fake-png-bytes"]


def test_pdf_ocr_fallback_is_evaluated_per_page(monkeypatch):
    # A blank (no-text) page plus a real-text page in the same document -
    # only the blank one should trigger OCR.
    import fitz  # pymupdf

    doc = fitz.open()
    doc.new_page()  # page 1: blank, no text
    page_2 = doc.new_page()  # page 2: real embedded text
    page_2.insert_text((72, 72), "Real embedded text on page two")
    raw_bytes = doc.tobytes()
    doc.close()

    ocr_calls = []

    def _fake_render(raw_bytes, page_number):
        return b"fake-png-bytes"

    def _fake_ocr(image_bytes):
        ocr_calls.append(image_bytes)
        return "OCR'd page one"

    monkeypatch.setattr(extraction_module, "_render_pdf_page_to_png", _fake_render)
    monkeypatch.setattr(extraction_module, "_ocr_image_bytes", _fake_ocr)

    pages = extract_pages(raw_bytes, "mixed.pdf")

    assert len(pages) == 2
    assert pages[0] == (1, "OCR'd page one")
    assert "Real embedded text on page two" in pages[1][1]
    assert len(ocr_calls) == 1  # OCR ran exactly once - only for page 1


def test_image_file_is_ocrd_directly(monkeypatch):
    calls = []

    def _fake_ocr(image_bytes):
        calls.append(image_bytes)
        return "Hello from an image"

    monkeypatch.setattr(extraction_module, "_ocr_image_bytes", _fake_ocr)

    raw_bytes = b"not-a-real-image-just-bytes"
    for ext in ("photo.jpg", "photo.jpeg", "photo.png"):
        pages = extract_pages(raw_bytes, ext)
        assert pages == [(1, "Hello from an image")]

    assert calls == [raw_bytes] * 3
