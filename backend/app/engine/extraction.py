"""
Raw file bytes -> a list of (page_number, text) tuples.

Supported formats:
  - PDF  (pdfplumber): one entry per real page.
  - DOCX (python-docx): a .docx file has no native concept of a fixed page
    (pagination is a rendering-time detail computed by Word, not stored in
    the file), so paragraphs are grouped into synthetic "pages" once
    roughly _DOCX_PAGE_CHAR_TARGET characters accumulate. This gives
    citations ("page 3") a stable, deterministic unit to point at, but it
    will NOT match the page numbers a reader sees in Word/Word Online -
    documented here and in the README.
  - TXT  (plain decode): treated as a single page.

Anything else raises UnsupportedFileTypeError so the API layer can record
a clear Document.error_message instead of crashing.
"""

import io
from typing import List, Tuple

PageText = Tuple[int, str]

_DOCX_PAGE_CHAR_TARGET = 2000


class UnsupportedFileTypeError(ValueError):
    """Raised when extract_pages() is given a file type it can't handle."""


def extract_pages(raw_bytes: bytes, filename: str) -> List[PageText]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        return _extract_pdf(raw_bytes)
    if ext == "docx":
        return _extract_docx(raw_bytes)
    if ext == "txt":
        return _extract_txt(raw_bytes)

    raise UnsupportedFileTypeError(
        f"Unsupported file type '.{ext}' (filename={filename!r}). "
        "Supported types: .pdf, .docx, .txt"
    )


def _extract_pdf(raw_bytes: bytes) -> List[PageText]:
    import pdfplumber

    pages: List[PageText] = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append((index, text))
    return pages


def _extract_docx(raw_bytes: bytes) -> List[PageText]:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(raw_bytes))

    pages: List[PageText] = []
    buffer: List[str] = []
    buffer_len = 0

    for paragraph in document.paragraphs:
        text = paragraph.text
        if not text.strip():
            continue
        buffer.append(text)
        buffer_len += len(text)
        if buffer_len >= _DOCX_PAGE_CHAR_TARGET:
            pages.append((len(pages) + 1, "\n".join(buffer)))
            buffer = []
            buffer_len = 0

    if buffer:
        pages.append((len(pages) + 1, "\n".join(buffer)))
    if not pages:
        pages.append((1, ""))

    return pages


def _extract_txt(raw_bytes: bytes) -> List[PageText]:
    text = raw_bytes.decode("utf-8", errors="replace")
    return [(1, text)]
