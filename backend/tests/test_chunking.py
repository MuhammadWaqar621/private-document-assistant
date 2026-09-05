"""Unit tests for engine/chunking.py - boundary cases around
CHUNK_CHAR_MAX/CHUNK_CHAR_TARGET, plus confirming page_number is preserved
per chunk. Pure functions, no I/O - no fixtures or mocking needed."""

from app.engine.chunking import CHUNK_CHAR_MAX, CHUNK_CHAR_TARGET, chunk_pages


def test_empty_page_produces_no_chunks():
    assert chunk_pages([(1, "")]) == []


def test_whitespace_only_page_produces_no_chunks():
    assert chunk_pages([(1, "   \n\n   ")]) == []


def test_page_just_under_the_max_stays_a_single_chunk():
    text = "x" * (CHUNK_CHAR_MAX - 1)
    chunks = chunk_pages([(1, text)])

    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].page_number == 1
    assert chunks[0].chunk_index == 0


def test_page_exactly_at_the_max_stays_a_single_chunk():
    # chunk_pages splits when len(text) > CHUNK_CHAR_MAX, so exactly
    # CHUNK_CHAR_MAX characters is still the single-chunk boundary.
    text = "x" * CHUNK_CHAR_MAX
    chunks = chunk_pages([(1, text)])

    assert len(chunks) == 1
    assert chunks[0].text == text


def test_page_just_over_the_max_gets_split():
    # One character past the threshold, structured as two paragraphs so
    # the splitter has a paragraph boundary to prefer over a hard cut.
    first_paragraph = "a" * (CHUNK_CHAR_MAX // 2)
    second_paragraph = "b" * (CHUNK_CHAR_MAX // 2 + 1)
    text = f"{first_paragraph}\n\n{second_paragraph}"
    assert len(text) > CHUNK_CHAR_MAX

    chunks = chunk_pages([(1, text)])

    assert len(chunks) > 1
    assert all(chunk.page_number == 1 for chunk in chunks)
    # Every chunk should stay at or under the max split size.
    assert all(len(chunk.text) <= CHUNK_CHAR_MAX for chunk in chunks)
    # No text content should be lost across the split (paragraphs rejoin
    # with the same "\n\n" separator _split_long_text uses internally).
    assert first_paragraph in chunks[0].text


def test_page_requiring_many_chunks_splits_into_several_pieces():
    # Several long paragraphs, well beyond a single split.
    paragraphs = ["p" * (CHUNK_CHAR_TARGET) for _ in range(6)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_pages([(1, text)])

    assert len(chunks) >= 3
    assert all(chunk.page_number == 1 for chunk in chunks)
    # chunk_index increases monotonically from 0.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_hard_cut_when_a_single_paragraph_exceeds_the_max():
    # A single "paragraph" (no \n\n or \n breaks at all) longer than
    # CHUNK_CHAR_MAX has no natural boundary to split on, so
    # _split_long_text must fall back to a hard character-based cut.
    text = "z" * (CHUNK_CHAR_MAX * 3)

    chunks = chunk_pages([(1, text)])

    assert len(chunks) >= 3
    assert all(len(chunk.text) <= CHUNK_CHAR_MAX for chunk in chunks)
    # Reassembling every piece recovers the original text exactly (a hard
    # cut must not drop or duplicate any characters).
    assert "".join(chunk.text for chunk in chunks) == text


def test_page_number_is_preserved_per_chunk_across_multiple_pages():
    short_text = "short page content"
    long_text = "\n\n".join(["long paragraph " * 60 for _ in range(4)])
    assert len(long_text) > CHUNK_CHAR_MAX

    pages = [(1, short_text), (2, long_text), (3, short_text)]
    chunks = chunk_pages(pages)

    # Page 1 -> exactly one chunk.
    page_1_chunks = [c for c in chunks if c.page_number == 1]
    assert len(page_1_chunks) == 1
    assert page_1_chunks[0].text == short_text

    # Page 2 -> split into multiple chunks, all tagged page_number=2.
    page_2_chunks = [c for c in chunks if c.page_number == 2]
    assert len(page_2_chunks) > 1

    # Page 3 -> exactly one chunk again.
    page_3_chunks = [c for c in chunks if c.page_number == 3]
    assert len(page_3_chunks) == 1
    assert page_3_chunks[0].text == short_text

    # chunk_index is a single monotonically increasing sequence across the
    # whole document, not reset per page.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
