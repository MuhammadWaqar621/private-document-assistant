"""
Retrieval + streaming chat completion - the actual RAG logic.

`retrieve()` embeds a question and searches Qdrant, always scoped to
`user_id` and optionally also to `chat_id` - see qdrant_client.search() for
the tenant-isolation filter this relies on. By default (`chat_id=None`)
retrieval draws from every chat the calling user owns; passing a `chat_id`
narrows it to just that one chat's uploads. `stream_answer()` builds a
prompt from the retrieved chunks + prior chat history (both passed in as
plain dicts/dataclasses, never ORM objects) and streams tokens back from
the currently-selected chat provider (Groq by default, or Azure OpenAI -
see app/engine/llm_provider.py) as an async generator, so the API layer
can forward them to the client as they arrive instead of waiting for the
whole answer. Embeddings are always Azure OpenAI regardless of that
provider choice - Groq has no embeddings API.
"""

import os
from typing import AsyncGenerator, List, Optional, TypedDict

from app.engine.azure_client import get_embedding_client, get_embedding_config
from app.engine.llm_provider import get_active_chat_provider
from app.engine.qdrant_client import SearchResult, search

DEFAULT_TOP_K = 5

# Qdrant's search() returns its top-k nearest points regardless of how
# semantically irrelevant they are, unless a score_threshold is passed -
# without this, a chat with ANY document would always treat retrieval as
# "found something" even for a completely unrelated question. Calibrated
# against this project's real Azure embedding deployment: genuinely
# relevant matches scored ~0.79-0.85, genuinely irrelevant ones ~0.71-0.72
# (cosine similarity) - 0.75 cleanly separates them. This is RAG policy
# (what counts as relevant enough to ground an answer in), so it lives
# here rather than in qdrant_client.py's generic search() wrapper.
MIN_RELEVANCE_SCORE = float(os.getenv("RAG_MIN_RELEVANCE_SCORE", "0.75"))

# Used when at least one matching chunk was retrieved: answer strictly
# from the provided excerpts, never outside knowledge.
GROUNDED_SYSTEM_PROMPT = (
    "You are QueryNest, a document question-answering assistant. Answer the "
    "user's question using ONLY the information in the document excerpts "
    "provided below - never use outside knowledge. Each excerpt is labeled "
    "with its source filename and page number; cite them inline like "
    "(filename.pdf, p.3) when you use one. If the excerpts don't contain "
    "enough information to answer, say so plainly instead of guessing."
)

# Used when the caller has confirmed the user has at least one ready
# document somewhere in scope (see app/api/messages.py's hard gate - a
# user with literally zero documents never reaches this code path at all),
# but this specific question's retrieval matched no chunks. Rather than a
# flat refusal, the LLM may fall back to its own general knowledge - but
# must say so plainly, so the user can never mistake a general-knowledge
# answer for one grounded in their own documents.
UNGROUNDED_FALLBACK_SYSTEM_PROMPT = (
    "You are QueryNest, a document question-answering assistant. No excerpts "
    "from the user's uploaded documents matched this question. You may "
    "answer using your own general knowledge instead, but you MUST start "
    "your reply with a short, clear disclosure that this answer is not "
    "based on the user's uploaded documents (for example: 'I couldn't find "
    "anything about this in your documents, but here's what I know "
    "generally:') before giving the answer."
)

# Used when the caller has confirmed (a real DB check - see
# app/api/messages.py's has_any_ready_document hard gate) that the user has
# NO ready documents anywhere in scope at all. A real LLM call (not a
# hardcoded string) so greetings and "what is QueryNest"/"who built this"
# get a natural reply in the model's own words, while still refusing -
# rather than answering from general knowledge - any genuine content
# question, since there is nothing uploaded to ground it in yet. The
# security-relevant gate (whether this function is even reached) stays a
# code-level DB fact in messages.py, never something a prompt alone
# decides; this system prompt only controls the *wording* of the reply for
# that already-gated case.
ONBOARDING_SYSTEM_PROMPT = (
    "You are QueryNest, a private document chat assistant developed by "
    "Muhammad Waqar (waqarsahi621@gmail.com). This user has not uploaded "
    "any documents yet. Greet them naturally if they say hello, and if they "
    "ask what QueryNest is or who built it, answer using the facts above in "
    "your own words. If they ask ANY question that would require real "
    "information or facts to answer - anything beyond a greeting or a "
    "question about QueryNest itself - tell them clearly and briefly that "
    "you need them to upload a document first before you can answer that, "
    "and do NOT attempt to answer it yourself using general knowledge."
)


class HistoryMessage(TypedDict):
    role: str  # "user" | "assistant"
    content: str


def _history_messages(history: Optional[List[HistoryMessage]]) -> List[dict]:
    """Prior turns as plain {"role", "content"} dicts, dropping anything
    with an unrecognized role or empty content. Shared by _build_messages()
    (grounded/fallback prompts) and stream_onboarding_reply() below so the
    filtering logic isn't duplicated."""
    messages: List[dict] = []
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    return messages


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

    return search(
        query_embedding,
        user_id=user_id,
        chat_id=chat_id,
        top_k=top_k,
        score_threshold=MIN_RELEVANCE_SCORE,
    )


def _build_context_block(chunks: List[SearchResult]) -> str:
    return "\n\n---\n\n".join(f"[{chunk.filename}, p.{chunk.page_number}]\n{chunk.text}" for chunk in chunks)


def _build_messages(
    question: str, chunks: List[SearchResult], history: Optional[List[HistoryMessage]]
) -> List[dict]:
    # See app/api/messages.py's hard gate: this function is only ever
    # reached when the user has at least one ready document somewhere in
    # scope. An empty `chunks` here means THIS question just didn't match
    # any of them - a different situation from having no documents at all -
    # so the general-knowledge-with-disclosure prompt applies instead of a
    # flat refusal.
    system_prompt = GROUNDED_SYSTEM_PROMPT if chunks else UNGROUNDED_FALLBACK_SYSTEM_PROMPT
    messages: List[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(_history_messages(history))

    if chunks:
        context_block = _build_context_block(chunks)
        messages.append(
            {
                "role": "user",
                "content": f"Document excerpts:\n\n{context_block}\n\nQuestion: {question}",
            }
        )
    else:
        messages.append({"role": "user", "content": question})

    return messages


async def stream_answer(
    question: str,
    chunks: List[SearchResult],
    history: Optional[List[HistoryMessage]] = None,
) -> AsyncGenerator[str, None]:
    """Stream the assistant's answer token-by-token as it arrives from the
    currently-selected chat provider (Groq by default, or Azure OpenAI -
    see app/engine/llm_provider.py). Yields plain str tokens (deltas),
    never a full ORM/HTTP object - the API layer decides how to frame them
    (SSE, etc.)."""
    provider = get_active_chat_provider()  # caller must check azure_ai_configured() first

    messages = _build_messages(question, chunks, history)

    stream = await provider.client.chat.completions.create(
        model=provider.model,
        messages=messages,
        stream=True,
    )
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content


async def stream_onboarding_reply(
    question: str,
    history: Optional[List[HistoryMessage]] = None,
) -> AsyncGenerator[str, None]:
    """Stream a reply for a user with NO ready documents anywhere in scope
    (see app/api/messages.py's has_any_ready_document hard gate - a real DB
    check, unchanged, that decides whether this function is even reached).

    A genuine LLM call under ONBOARDING_SYSTEM_PROMPT, not a hardcoded
    string: this lets a greeting or a "what is QueryNest"/"who built this"
    question get a natural reply in the model's own words, while the
    system prompt explicitly instructs the model to refuse - not answer
    from general knowledge - any question that actually needs real
    information, since there is nothing uploaded yet to ground it in.
    Takes no `chunks`/`user_id`/`chat_id` - no Qdrant retrieval happens
    here at all. Streams from the same currently-selected chat provider as
    stream_answer() (see app/engine/llm_provider.py)."""
    provider = get_active_chat_provider()  # caller must check azure_ai_configured() first

    messages: List[dict] = [{"role": "system", "content": ONBOARDING_SYSTEM_PROMPT}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": question})

    stream = await provider.client.chat.completions.create(
        model=provider.model,
        messages=messages,
        stream=True,
    )
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
