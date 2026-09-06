"""
Retrieval + agentic streaming chat completion - the actual RAG logic.

`retrieve()` embeds a question and searches Qdrant, always scoped to
`user_id` and optionally also to `chat_id` - see qdrant_client.search() for
the tenant-isolation filter this relies on. By default (`chat_id=None`)
retrieval draws from every chat the calling user owns; passing a `chat_id`
narrows it to just that one chat's uploads.

`stream_agentic_reply()` is the single entrypoint app/api/messages.py
calls: the model itself decides, via a real tool call
(`search_documents`), whether this question needs retrieval at all - there
is no Python branching between prompts based on whether the user "has any
documents." Embeddings are always Azure OpenAI regardless of which chat
provider is active (Groq by default, or Azure OpenAI - see
app/engine/llm_provider.py) - Groq has no embeddings API.
"""

import json
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

# --- Agentic tool-calling path (stream_agentic_reply) -----------------------
#
# A single system prompt and one real tool the model decides whether to
# call - including for greetings/small-talk/"what is QueryNest" questions,
# which it answers directly in its own words rather than via regex
# detection or fixed Python branching on "does this user have any ready
# document." The actual security boundary is NOT a prompt-level decision:
# search_documents' execution always filters by the server-supplied
# user_id (and chat_id, when scope="chat") - the model supplies only the
# search query text, never whose documents to search.
AGENT_SYSTEM_PROMPT = (
    "You are QueryNest, a private document chat assistant developed by "
    "Muhammad Waqar (waqarsahi621@gmail.com). Users upload their own "
    "documents (PDFs, Word docs, text files, images) and ask questions "
    "about them.\n\n"
    "You have a tool, search_documents, that searches the current user's "
    "own uploaded documents for a query. For greetings, small talk, or "
    "questions about QueryNest itself (what it is, who built it), answer "
    "directly in your own words - don't call the tool for those.\n\n"
    "For every OTHER question, you MUST call search_documents first before "
    "answering - never answer a factual question from memory without "
    "having called it, even if you are confident you already know the "
    "answer.\n\n"
    "If search_documents returns excerpts, answer using ONLY those "
    "excerpts - never outside knowledge. Each excerpt is labeled with its "
    "source filename and page number; cite them inline like "
    "(filename.pdf, p.3) when you use one. If the excerpts don't contain "
    "enough information, say so plainly instead of guessing.\n\n"
    "If search_documents returns no results - whether because the user "
    "has no documents yet or none matched this question - answer directly "
    "from your own general knowledge instead. Do not mention the search, "
    "the lack of results, or that this answer isn't based on the user's "
    "documents - just answer the question plainly, the way you would if "
    "there were no document feature at all."
)

SEARCH_DOCUMENTS_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search the current user's own uploaded documents for content "
                "relevant to a query. Returns the most relevant excerpts, or "
                "none if nothing relevant was found."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The search query - typically the user's question, "
                            "or a focused rephrasing of it."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    }
]


class HistoryMessage(TypedDict):
    role: str  # "user" | "assistant"
    content: str


def _history_messages(history: Optional[List[HistoryMessage]]) -> List[dict]:
    """Prior turns as plain {"role", "content"} dicts, dropping anything
    with an unrecognized role or empty content."""
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


async def stream_agentic_reply(
    question: str,
    user_id: int,
    chat_id: Optional[int],
    history: Optional[List[HistoryMessage]] = None,
) -> AsyncGenerator[str, None]:
    """The single entrypoint the message-send endpoint calls - see the
    module docstring and AGENT_SYSTEM_PROMPT above.

    `user_id` and `chat_id` are supplied by the caller from the
    authenticated session/request, never by the model - the tool schema
    exposed to the model only has a `query` string parameter. This is the
    actual isolation boundary; the model has no way to name a different
    user or chat to search.

    Two-phase, correctly streamed: the first call is genuinely streamed
    with the tool available - if the model answers directly (greetings,
    "what is QueryNest", etc.), those are real incremental tokens with no
    second round-trip. Only if the model actually requests the tool does
    a second call happen (also streamed) for the final answer, after the
    tool has actually run and its real result is appended to the
    conversation.
    """
    provider = get_active_chat_provider()  # caller must check azure_ai_configured() first

    messages: List[dict] = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": question})

    stream = await provider.client.chat.completions.create(
        model=provider.model,
        messages=messages,
        tools=SEARCH_DOCUMENTS_TOOL,
        tool_choice="auto",
        stream=True,
    )

    # Accumulate tool-call deltas by index (the streaming API sends a
    # function call's name/arguments in fragments across multiple chunks,
    # same as content deltas) while forwarding any real content tokens
    # immediately - a response with no tool call streams entirely here,
    # with nothing left to do afterward.
    tool_calls_acc: dict[int, dict] = {}
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
        if delta and delta.tool_calls:
            for tc in delta.tool_calls:
                entry = tool_calls_acc.setdefault(tc.index, {"id": None, "name": None, "arguments": ""})
                if tc.id:
                    entry["id"] = tc.id
                if tc.function and tc.function.name:
                    entry["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    entry["arguments"] += tc.function.arguments

    if not tool_calls_acc:
        return

    assistant_tool_calls = []
    tool_result_messages = []
    for index in sorted(tool_calls_acc):
        entry = tool_calls_acc[index]
        try:
            args = json.loads(entry["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        query = args.get("query") or question

        # The real tool implementation: reuses retrieve() as-is, always
        # scoped to the server-supplied user_id/chat_id - never anything
        # the model provided.
        chunks = retrieve(query, user_id=user_id, chat_id=chat_id)
        result_text = _build_context_block(chunks) if chunks else "(no matching document excerpts were found)"

        assistant_tool_calls.append(
            {
                "id": entry["id"],
                "type": "function",
                "function": {"name": entry["name"] or "search_documents", "arguments": entry["arguments"]},
            }
        )
        tool_result_messages.append(
            {"role": "tool", "tool_call_id": entry["id"], "content": result_text}
        )

    messages.append({"role": "assistant", "content": None, "tool_calls": assistant_tool_calls})
    messages.extend(tool_result_messages)

    final_stream = await provider.client.chat.completions.create(
        model=provider.model,
        messages=messages,
        stream=True,
    )
    async for event in final_stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
