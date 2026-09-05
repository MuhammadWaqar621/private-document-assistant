"""
Raw file bytes -> a list of (page_number, text) tuples.

Supported formats:
  - PDF  (pdfplumber): one entry per real page. A page whose extracted text
    is empty or near-empty (see _OCR_FALLBACK_MIN_CHARS below) is treated
    as a scanned page with no embedded text layer: that specific page is
    rendered to a raster image (pymupdf/fitz) and OCR'd (EasyOCR) instead.
    This is decided per-page, so a partially-scanned PDF (some real text
    pages, some scanned pages) gets the right treatment for each page
    rather than an all-or-nothing choice for the whole document.
  - DOCX (python-docx): a .docx file has no native concept of a fixed page
    (pagination is a rendering-time detail computed by Word, not stored in
    the file), so paragraphs are grouped into synthetic "pages" once
    roughly _DOCX_PAGE_CHAR_TARGET characters accumulate. This gives
    citations ("page 3") a stable, deterministic unit to point at, but it
    will NOT match the page numbers a reader sees in Word/Word Online -
    documented here and in the README. Legacy binary .doc is NOT
    supported (would need a separate toolchain - antiword/LibreOffice
    headless - out of scope here; see README).
  - TXT  (plain decode): treated as a single page.
  - JPG/JPEG/PNG (EasyOCR): the whole image is OCR'd and returned as a
    single page (page_number=1).

Anything else raises UnsupportedFileTypeError so the API layer can record
a clear Document.error_message instead of crashing.

OCR (EasyOCR) is CPU-only and its Reader is expensive to construct (it
loads model weights) - see _get_ocr_reader()'s @lru_cache, which mirrors
the module-level singleton pattern used in engine/azure_client.py.
"""

import io
from functools import lru_cache
from typing import List, Tuple

PageText = Tuple[int, str]

_DOCX_PAGE_CHAR_TARGET = 2000

# A pdfplumber-extracted page with fewer than this many non-whitespace
# characters is treated as having no real text layer (i.e. a scanned
# image page) and is OCR'd instead. A small positive threshold rather than
# requiring fully empty text, since pdfplumber can occasionally pull a
# stray character or two of noise (a page number, a watermark) out of an
# otherwise-scanned page.
_OCR_FALLBACK_MIN_CHARS = 20

# DPI used when rendering a scanned PDF page to an image for OCR - a
# balance between OCR accuracy (higher is better) and speed/memory (lower
# is faster). 200-300 is the commonly recommended range for OCR.
_OCR_RENDER_DPI = 250


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
    if ext in ("jpg", "jpeg", "png"):
        return _extract_image(raw_bytes)

    raise UnsupportedFileTypeError(
        f"Unsupported file type '.{ext}' (filename={filename!r}). "
        "Supported types: .pdf, .docx, .txt, .jpg, .jpeg, .png"
    )


@lru_cache
def _get_ocr_reader():
    """Construct the EasyOCR Reader exactly once (loading its model
    weights is expensive) rather than per call. CPU-only (gpu=False),
    matching the CPU-only torch install in requirements.txt."""
    import easyocr

    return easyocr.Reader(["en"], gpu=False)


def _ocr_image_bytes(image_bytes: bytes) -> str:
    """Run EasyOCR against raw image bytes (anything Pillow/OpenCV can
    decode - JPEG, PNG, or a PDF page rendered to PNG) and join the
    detected text fragments into one string, in reading order."""
    reader = _get_ocr_reader()
    fragments = reader.readtext(image_bytes, detail=0, paragraph=True)
    return "\n".join(fragments)


def _extract_image(raw_bytes: bytes) -> List[PageText]:
    return [(1, _ocr_image_bytes(raw_bytes))]


def _extract_pdf(raw_bytes: bytes) -> List[PageText]:
    import pdfplumber

    pages: List[PageText] = []
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if len(text.strip()) < _OCR_FALLBACK_MIN_CHARS:
                ocr_text = _ocr_pdf_page(raw_bytes, index)
                if ocr_text.strip():
                    text = ocr_text
            pages.append((index, text))
    return pages


def _render_pdf_page_to_png(raw_bytes: bytes, page_number: int) -> bytes:
    """Render page `page_number` (1-indexed) of the PDF to PNG bytes via
    pymupdf, at _OCR_RENDER_DPI - used as a fallback for pages pdfplumber
    found no (or negligible) embedded text on, i.e. scanned pages. No
    system binary is needed (unlike poppler/ghostscript-based approaches)
    - pymupdf bundles its own rendering."""
    import fitz  # pymupdf

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    try:
        page = doc[page_number - 1]
        pixmap = page.get_pixmap(dpi=_OCR_RENDER_DPI)
        return pixmap.tobytes("png")
    finally:
        doc.close()


def _ocr_pdf_page(raw_bytes: bytes, page_number: int) -> str:
    png_bytes = _render_pdf_page_to_png(raw_bytes, page_number)
    return _ocr_image_bytes(png_bytes)


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
