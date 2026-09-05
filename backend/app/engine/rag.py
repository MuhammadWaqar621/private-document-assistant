"""
Retrieval + streaming chat completion - the actual RAG logic.

`retrieve()` embeds a question and searches Qdrant, always scoped to
`user_id` and optionally also to `chat_id` - see qdrant_client.search() for
the tenant-isolation filter this relies on. By default (`chat_id=None`)
retrieval draws from every chat the calling user owns; passing a `chat_id`
narrows it to just that one chat's uploads. `stream_answer()` builds a
prompt from the retrieved chunks + prior chat history (both passed in as
plain dicts/dataclasses, never ORM objects) and streams tokens back from
the Azure chat deployment as an async generator, so the API layer can
forward them to the client as they arrive instead of waiting for the whole
answer.
"""

from typing import AsyncGenerator, List, Optional, TypedDict

from app.engine.azure_client import (
    get_async_chat_client,
    get_chat_config,
    get_embedding_client,
    get_embedding_config,
)
from app.engine.qdrant_client import SearchResult, search

DEFAULT_TOP_K = 5

SYSTEM_PROMPT = (
    "You are querynest, a document question-answering assistant. Answer the "
    "user's question using ONLY the information in the document excerpts "
    "provided below - never use outside knowledge. Each excerpt is labeled "
    "with its source filename and page number; cite them inline like "
    "(filename.pdf, p.3) when you use one. If the excerpts don't contain "
    "enough information to answer, say so plainly instead of guessing."
)


class HistoryMessage(TypedDict):
    role: str  # "user" | "assistant"
    content: str


def retrieve(
    query: str, user_id: int, chat_id: Optional[int] = None, top_k: int = DEFAULT_TOP_K
) -> List[SearchResult]:
    """Embed `query` and search Qdrant, always scoped to `user_id`. Pass
    `chat_id` to additionally restrict retrieval to just that chat's
    uploads; leave it `None` (the default) to search across every chat the
    user owns."""
    embedding_client = get_embedding_client()
    embedding_config = get_embedding_config()
    assert embedding_config is not None  # caller must check azure_ai_configured() first

    response = embedding_client.embeddings.create(model=embedding_config.model, input=[query])
    query_embedding = response.data[0].embedding

    return search(query_embedding, user_id=user_id, chat_id=chat_id, top_k=top_k)


def _build_context_block(chunks: List[SearchResult]) -> str:
    if not chunks:
        return "(no matching document excerpts were found)"
    return "\n\n---\n\n".join(f"[{chunk.filename}, p.{chunk.page_number}]\n{chunk.text}" for chunk in chunks)


def _build_messages(
    question: str, chunks: List[SearchResult], history: Optional[List[HistoryMessage]]
) -> List[dict]:
    messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    context_block = _build_context_block(chunks)
    messages.append(
        {
            "role": "user",
            "content": f"Document excerpts:\n\n{context_block}\n\nQuestion: {question}",
        }
    )
    return messages


async def stream_answer(
    question: str,
    chunks: List[SearchResult],
    history: Optional[List[HistoryMessage]] = None,
) -> AsyncGenerator[str, None]:
    """Stream the assistant's answer token-by-token as it arrives from
    Azure OpenAI. Yields plain str tokens (deltas), never a full ORM/HTTP
    object - the API layer decides how to frame them (SSE, etc.)."""
    client = get_async_chat_client()
    config = get_chat_config()
    assert config is not None  # caller must check azure_ai_configured() first

    messages = _build_messages(question, chunks, history)

    stream = await client.chat.completions.create(
        model=config.model,
        messages=messages,
        stream=True,
    )
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
