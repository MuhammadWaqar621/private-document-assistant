"""
(page_number, text) tuples -> chunks sized for embedding.

Token counts aren't computed exactly here - a simple char-based
approximation (~4 characters per token for English text, a common rule of
thumb) is good enough for sizing chunks, targeting roughly 500-800 tokens
(~2000-3200 characters) per chunk. Most pages are shorter than that and
stay as a single chunk; only unusually long pages get split further, and
splitting prefers paragraph/line boundaries so a chunk doesn't get cut
mid-sentence. Every chunk keeps the page_number of the page it came from.
"""

from dataclasses import dataclass
from typing import List, Tuple

PageText = Tuple[int, str]

CHUNK_CHAR_TARGET = 2400  # ~600 tokens
CHUNK_CHAR_MAX = 3200  # ~800 tokens - pages longer than this get split


@dataclass(frozen=True)
class Chunk:
    chunk_index: int  # position within the whole document, 0-based
    page_number: int
    text: str


def chunk_pages(pages: List[PageText]) -> List[Chunk]:
    chunks: List[Chunk] = []
    for page_number, text in pages:
        text = text.strip()
        if not text:
            continue
        if len(text) <= CHUNK_CHAR_MAX:
            chunks.append(Chunk(chunk_index=len(chunks), page_number=page_number, text=text))
            continue
        for piece in _split_long_text(text):
            chunks.append(Chunk(chunk_index=len(chunks), page_number=page_number, text=piece))
    return chunks


def _split_long_text(text: str) -> List[str]:
    """Split text longer than CHUNK_CHAR_MAX into ~CHUNK_CHAR_TARGET-sized
    pieces, preferring paragraph breaks, then line breaks, then a hard cut
    for a single paragraph that is itself longer than CHUNK_CHAR_MAX."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [p for p in text.split("\n") if p.strip()] or [text]

    pieces: List[str] = []
    buffer = ""

    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) > CHUNK_CHAR_MAX and buffer:
            pieces.append(buffer)
            buffer = paragraph
        else:
            buffer = candidate

        while len(buffer) > CHUNK_CHAR_MAX:
            pieces.append(buffer[:CHUNK_CHAR_TARGET])
            buffer = buffer[CHUNK_CHAR_TARGET:]

    if buffer:
        pieces.append(buffer)

    return pieces
