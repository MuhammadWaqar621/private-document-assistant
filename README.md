# QueryNest

Your own private AI assistant for your own documents. Upload files that are
yours alone, and get answers grounded strictly in their content — never in
public training data. General-purpose tools like ChatGPT or Claude have
never seen this content and can't answer questions about it; QueryNest
exists specifically to let you interrogate your own private, secure
documents without ever sending them to a third-party AI product.

Built as a portfolio project to demonstrate a production-shaped
retrieval-augmented-generation stack end to end: chunking and embedding
documents, storing vectors in a purpose-built vector database, and
grounding an LLM's answers in retrieved context rather than letting it
hallucinate freely - with every document scoped to the account (and, by
default, the specific conversation) that uploaded it.

> **Status:** Feature-complete for its intended scope. Document ingestion
> (PDF/DOCX/TXT/JPG/PNG → chunk → embed → Qdrant, with EasyOCR filling in
> for images and scanned PDF pages that have no extractable text layer),
> the RAG chat pipeline (retrieve → stream an answer back over
> Server-Sent Events from either Groq or Azure OpenAI, selectable via
> `LLM_PROVIDER`), speech (Groq Whisper mic-to-text, Groq PlayAI
> text-to-speech per message), authentication (JWT + forgot-password), and
> chat/message history all work end-to-end against the docker-compose
> stack, backed by an automated test suite (see "Running tests" below)
> covering the multi-tenant isolation boundary, chunking/extraction/OCR
> edge cases, provider selection, and the API layer's auth/ownership
> checks. See Roadmap for the deliberate tradeoffs (synchronous ingestion,
> no background task queue) left as explicitly-scoped-out production
> upgrades.

## Screenshots

Captured from the actual running docker-compose stack (not mockups) - the
config-status pattern referenced throughout this README (see "Document
retrieval scope" and "Environment variables" below) is visible in the
first and second screenshots exactly as it appears with this deployment's
real `.env`: Azure OpenAI and SMTP configured.

| | |
|---|---|
| **Home - live config status** | **Login** |
| [![Home page showing the backend configuration status panel](docs/screenshots/home-config-status.png)](docs/screenshots/home-config-status.png) | [![Login page](docs/screenshots/login.png)](docs/screenshots/login.png) |
| **Signup** | **Chat - document uploaded and ready** |
| [![Signup page](docs/screenshots/signup.png)](docs/screenshots/signup.png) | [![Chat UI with a PDF uploaded and its status showing ready](docs/screenshots/chat-document-uploaded.png)](docs/screenshots/chat-document-uploaded.png) |
| **Chat - a real streamed, cited answer** | |
| [![Chat UI showing a completed streamed answer with a page citation](docs/screenshots/chat-streamed-answer.png)](docs/screenshots/chat-streamed-answer.png) | |

The final screenshot is a real round trip through the whole pipeline
described below: a PDF fixture was uploaded and ingested (extract → chunk
→ embed → Qdrant), a question was asked, and Azure OpenAI's streamed
answer came back grounded in - and citing - the uploaded document's actual
text (`sample.pdf, p.1`).

## Architecture

```mermaid
flowchart TB
    subgraph Client
        FE["Frontend<br/>React + Vite + TypeScript + Tailwind"]
    end

    subgraph Backend["Backend - FastAPI"]
        API["API layer<br/>app/api/*.py<br/>auth, ownership checks,<br/>DB persistence, SSE streaming"]
        ENGINE["engine/ package<br/>extraction (+ EasyOCR fallback) · chunking · embedding<br/>Qdrant search · RAG prompt/streaming · provider selection<br/>(zero dependency on API/DB/auth code)"]
    end

    PG[("Postgres<br/>users, password_reset_tokens,<br/>chats, messages,<br/>documents (status/metadata)")]
    QD[("Qdrant<br/>vector store<br/>every point tagged with<br/>user_id + chat_id")]
    AZ["Azure OpenAI<br/>embeddings (always) +<br/>chat completions (if LLM_PROVIDER=azure)"]
    GQ["Groq<br/>chat completions (if LLM_PROVIDER=groq, default) +<br/>Whisper STT + PlayAI TTS"]

    FE -- "HTTP /api/* + SSE" --> API
    API -- "plain args in, plain data out" --> ENGINE
    API -- "SQLAlchemy" --> PG
    ENGINE -- "vector upsert/search,<br/>always filtered by user_id,<br/>optionally by chat_id" --> QD
    ENGINE -- "embeddings, and chat<br/>completions when selected" --> AZ
    ENGINE -- "chat completions (default),<br/>speech-to-text, text-to-speech" --> GQ
```

**Document retrieval scope:** by default, a question asked in *any* chat
draws on *every* document that user has uploaded, across *all* of their
chats - so a document uploaded in chat B can answer a question asked in
chat A, as long as both belong to the same user. Checking **"Only search
this chat's documents"** in the message input narrows retrieval down to
just the currently-selected chat's uploads. In both modes, retrieval is
**always** scoped to the authenticated user - one user's documents are
never retrievable by another, regardless of scope. This isn't a UI
convention - `engine/qdrant_client.py`'s `search()` applies a `user_id`
`must` filter condition unconditionally on every vector search, and only
adds a `chat_id` `must` condition when the caller explicitly asks for the
chat-scoped mode. See "Document upload + RAG chat flow" below for exactly
how the two modes map to the `scope` field on
`POST /api/chats/{chat_id}/messages`.

- **Backend** — Python 3.11+, FastAPI, SQLAlchemy + Alembic for migrations,
  Postgres for relational data (users, chats, messages, documents), Qdrant
  for vector search. Embeddings always go through `openai.AzureOpenAI` —
  this project targets **Azure OpenAI** specifically for embeddings, not
  the public OpenAI API. **Chat completions** are provider-selectable via
  `LLM_PROVIDER` (`"groq"`, the default, or `"azure"`) — see
  `app/engine/llm_provider.py` and "Chat provider selection" below. Groq's
  API is OpenAI-compatible, so the same `openai` package is reused for it
  too (pointed at Groq's base URL), and also backs speech-to-text
  (Whisper) and text-to-speech (PlayAI TTS) — see "Speech" below.
- **`app/engine/`** — the RAG engine (extraction + OCR, chunking,
  embedding, Qdrant access, retrieval, streaming chat, provider selection)
  is a self-contained package with **zero imports** from `app/api`,
  `app/models` (SQLAlchemy), or auth code. It reads its own configuration
  straight from environment variables and every function takes/returns
  plain Python values (ints, strings, bytes, dicts/dataclasses) — never an
  ORM object or a FastAPI Request/Response. This makes it independently
  testable and reusable outside this specific FastAPI app.
  `app/engine/groq_client.py` (Groq client construction for STT/TTS/chat)
  and `app/engine/llm_provider.py` (the `LLM_PROVIDER` selection layer)
  follow this same isolation contract, exactly like `azure_client.py` -
  they're part of the engine, not the API layer, even though
  `app/api/speech.py` is the thin API-layer wrapper that exposes the
  STT/TTS functions over HTTP (auth-checked, no DB/ownership involved).
  `app/api/documents.py` and `app/api/messages.py` are the *only* code
  that talks to both the DB/auth stack and `app/engine/` — they check
  auth/ownership, call a plain engine function, and persist the result.
  See `app/engine/__init__.py` for the full isolation contract.
- **Frontend** — React + TypeScript + Vite + Tailwind CSS.
- **Vector DB** — Qdrant, run locally via docker-compose (or pointed at
  Qdrant Cloud — see below).
- **Auth** — email/password only (JWT access + refresh tokens,
  `passlib`/bcrypt hashing), plus forgot/reset password via SMTP
  (`aiosmtplib`). See "Authentication & chat history" below.

## Document upload + RAG chat flow

1. **Upload** — `POST /api/chats/{chat_id}/documents` (multipart) saves
   the raw file to `storage/{user_id}/{document_id}/original.<ext>`,
   creates a `Document` row (`status=processing`), then runs ingestion:
   - `app/engine/extraction.py` pulls text out of the file - one entry per
     page for PDF (`pdfplumber`), a single page for TXT, a single page for
     JPG/JPEG/PNG (OCR'd via EasyOCR), and for DOCX (which has no native
     page concept) paragraphs are grouped into synthetic ~2000-character
     "pages" so citations still have a stable unit to point at (this won't
     match Word's own page numbers). See "Document types supported" below
     for the full picture, including the OCR fallback for scanned PDF
     pages.
   - `app/engine/chunking.py` splits page text into ~500-800 token
     (~2000-3200 character) chunks, char-based approximation, only
     splitting pages that are unusually long.
   - `app/engine/ingestion.py` embeds each chunk in batches via the Azure
     embeddings deployment (always Azure, regardless of `LLM_PROVIDER` -
     see "Chat provider selection" below) and upserts them into Qdrant
     (`app/engine/qdrant_client.py`), tagged with `document_id`,
     `user_id`, `chat_id`, `filename`, and `page_number`.
   - On success the `Document` row becomes `status=ready`; on any failure
     (unsupported file type, extraction error, embedding/Qdrant failure)
     it becomes `status=failed` with a human-readable `error_message` -
     the upload request itself never crashes.
2. **Ask a question** — `POST /api/chats/{chat_id}/messages` takes
   `{content, scope}` (`scope` is `"all"` or `"chat"`, defaulting to
   `"all"` when omitted), persists the user's message, and streams the
   reply from `app/engine/rag.stream_agentic_reply()` - a genuinely
   **agentic, tool-calling** pipeline, not Python branching between
   canned prompts. There is no `has_any_ready_document` gate and no
   separate "onboarding" code path anymore - the model itself decides,
   per message, whether it needs to search:
   - The model is given exactly one tool, `search_documents` (schema:
     `SEARCH_DOCUMENTS_TOOL` in `rag.py`), which takes a single `query`
     string - nothing else. It is streamed a first completion with
     `tools=[SEARCH_DOCUMENTS_TOOL]` and `tool_choice="auto"`, under
     `AGENT_SYSTEM_PROMPT`.
   - **For greetings, small talk, or questions about QueryNest itself**
     ("what is this", "who built it"), the model answers directly in that
     first streamed call - no tool call, no Qdrant query, no second
     completion. This is what makes "hi" from a user who has documents
     sitting in some *other* chat get a plain, natural greeting instead of
     an awkward "the provided excerpts don't contain any information..."
     refusal - the old three-bucket hardcoded design got this wrong,
     because it branched purely on "does this user have any ready document
     anywhere," never on what the message actually asked.
   - **For anything else**, the system prompt requires the model to call
     `search_documents` before answering - even a question it could
     answer from memory (e.g. "what's the capital of France") must go
     through the tool first, so the decision of whether an answer is
     grounded is never skipped. The endpoint executes the call for real:
     `app/engine/rag.retrieve()` embeds the model's `query` argument and
     searches Qdrant, filtered by `user_id` (always, from the
     authenticated session - **never** an argument the model can supply)
     and by `chat_id` only when `scope="chat"` (also supplied by the
     endpoint, not the model). The result is fed back as a `role="tool"`
     message, and a **second** streamed completion produces the final
     answer:
     - If excerpts came back, the model must answer using only those
       excerpts, citing `(filename, p.N)` inline, and say so plainly if
       they don't contain enough information - never fall back to outside
       knowledge silently.
     - If no excerpts came back (no documents yet, or none matched), the
       model may answer from general knowledge instead, but is required to
       open with an explicit disclosure that the answer isn't grounded in
       the user's documents (e.g. "I couldn't find anything about this in
       your documents, but here's what I know generally: ...") - verified
       live against the real Groq-backed stack for both a zero-document
       user and a document owner whose question didn't match anything.
   - The two arguments that actually matter for isolation -
     `user_id`/`chat_id` - are always supplied by `app/api/messages.py`
     from the authenticated request, captured as plain values before the
     streaming generator runs (not read lazily from the ORM `User`/`Chat`
     objects, which are already detached by the time a `StreamingResponse`
     generator executes). The model only ever supplies the free-text
     `query` string; it has no way to name a different user or chat to
     search, regardless of what the request body or conversation asks it
     to do. Verified live: a document uploaded by one user is never
     retrieved by a different user asking the identical question in their
     own chat.
   - Either way, the reply streams back over **Server-Sent Events**
     (`text/event-stream`) - real incremental tokens, not a spinner
     followed by the whole answer at once - and once the stream finishes,
     the full assistant reply is persisted as a `Message` row
     (`role=assistant`).
   - `scope="all"` (the default): retrieval draws from every document the
     current user has uploaded, across every chat they own - a document
     uploaded in chat B can answer a question asked in chat A.
   - `scope="chat"`: retrieval is additionally restricted to just the
     current chat's uploads - the frontend's "Only search this chat's
     documents" checkbox opts into this.
   - Either way, the `user_id` filter is unconditional - one user's
     documents are never retrievable by another, regardless of scope.
3. **Frontend** — the chat input uses a manual `fetch` + `ReadableStream`
   reader (not the browser `EventSource` API, which can't attach a Bearer
   token header) to render tokens as they arrive; see
   `frontend/src/lib/chatStream.ts`. A checkbox next to the input controls
   which `scope` value is sent. A mic button records a voice question
   (transcribed via `/api/speech/transcribe`) and a speaker button next to
   each assistant reply plays it back (`/api/speech/synthesize`) - see
   "Speech" below.

## Document types supported

| Type | How it's read | Notes |
|---|---|---|
| `.pdf` | `pdfplumber`, one entry per real page | Per-page OCR fallback (below) for scanned pages |
| `.docx` | `python-docx` | Modern OOXML only - see "Word support" below |
| `.txt` | plain UTF-8 decode (`errors="replace"`) | Treated as a single page |
| `.jpg` / `.jpeg` / `.png` | EasyOCR | Whole image OCR'd, returned as a single page |

**OCR (EasyOCR) for images and scanned PDF pages.** The owner specifically
asked for [EasyOCR](https://github.com/JaidedAI/EasyOCR) here (not
Tesseract, not a vision LLM). For an image upload, the whole image is
OCR'd directly. For a PDF, `app/engine/extraction.py` tries `pdfplumber`'s
normal text extraction on **every page independently**: if a page's
extracted text is empty or near-empty (under ~20 stripped characters - the
heuristic for "this page has no real text layer, it's a scanned image"),
that specific page is rendered to a PNG via `pymupdf`/`fitz` (no system
binary needed, unlike poppler/ghostscript-based approaches) at ~250 DPI
and OCR'd instead. This is decided **per page**, so a partially-scanned
PDF (some real-text pages, some scanned pages) gets the right treatment
for each page rather than an all-or-nothing choice for the whole document.
EasyOCR's `Reader` (CPU-only - `gpu=False`, matching the CPU-only PyTorch
install below) loads real ML model weights, so it's constructed once and
memoized (`@lru_cache`) rather than per call.

Because EasyOCR pulls in `torch`/`torchvision`, `backend/requirements.txt`
starts with a `--extra-index-url` line pointing at PyTorch's CPU-only
wheel index, so the Docker image gets the much smaller CPU-only builds
instead of CUDA builds it would never use (there's no GPU in this stack).
This still makes the backend image noticeably larger and slower to build
than before - see "Running tests" below for the actual numbers observed
building this project.

**Word support (a deliberate limitation).** Only modern `.docx` (OOXML) is
supported, via `python-docx`. Legacy binary `.doc` files are **not**
supported and are out of scope for this project - reading them would
require a completely separate toolchain (e.g. `antiword`, or LibreOffice
running headless to convert `.doc → .docx` first), which wasn't worth
adding just for a legacy format. A `.doc` upload isn't silently accepted
either - its extension simply isn't in `app/api/documents.py`'s
`ALLOWED_EXTENSIONS`, so `extract_pages()` raises `UnsupportedFileTypeError`
and the `Document` row lands as `status=failed` with a clear message,
exactly like any other unsupported type.

## Chat provider selection

Embeddings are **always** Azure OpenAI (`AZURE_EM_*`) - Groq has no
embeddings API, so this isn't configurable. **Chat completions**, however,
are selectable via `LLM_PROVIDER`:

- `LLM_PROVIDER=groq` (the **default** - used for any unset/blank/
  unrecognized value): chat completions come from Groq
  (`GROQ_API_KEY` + `GROQ_LLM_MODEL`, default
  `llama-3.3-70b-versatile`) - fast, and it's what this deployment uses
  out of the box.
- `LLM_PROVIDER=azure`: chat completions come from Azure OpenAI
  (`LLM_ENDPOINT`/`LLM_ENDPOINT_APIKEY`/`LLM_MODEL_NAME`), exactly as this
  project originally worked before Groq support was added.

`app/engine/llm_provider.py` is the single place that reads
`LLM_PROVIDER` and hands back the right (name, async client, model) tuple
- `app/engine/rag.py`'s `stream_agentic_reply()` calls
`get_active_chat_provider()` instead of reaching into
`azure_client.py`/`groq_client.py` directly, so it doesn't need to know
or care which provider is active - including whether that provider's
streamed response includes a tool call.

`app/engine/azure_client.py`'s `azure_ai_configured()` (kept under that
name for backward compatibility - this project originally supported only
Azure OpenAI, for both embeddings and chat) is the combined gate
`app/api/documents.py`/`app/api/messages.py` check before running
ingestion/chat: **embeddings configured AND whichever chat provider is
currently selected is configured** - never both providers' credentials at
once. `GET /api/config/status`'s `rag` group (see "Environment variables"
below) reports this same combined check, and also exposes which provider
is active via `"llm_provider"`.

## Speech

Powered by [Groq](https://groq.com) - `app/engine/groq_client.py` (same
isolation contract as `azure_client.py`: zero imports from `app/api`/
`app/models`/auth, reads env vars via `os.getenv` directly) wraps the
`openai` Python SDK pointed at Groq's OpenAI-compatible API
(`https://api.groq.com/openai/v1`), so no separate SDK dependency was
needed for STT/TTS/chat.

- **Speech-to-text (mic button):** the chat input has a mic button - click
  to start recording (browser `MediaRecorder` API), click again to stop.
  The recorded audio blob is POSTed to `POST /api/speech/transcribe`
  (multipart), which calls Groq's Whisper endpoint (`GROQ_STT_MODEL`,
  default `whisper-large-v3`) and returns `{"text": "..."}` - the frontend
  appends that text to (or replaces, if empty) whatever's already typed in
  the message box.
- **Text-to-speech (speaker button):** every assistant message has a
  speaker button next to it. Clicking it POSTs the message's full text to
  `POST /api/speech/synthesize`, which calls Groq's PlayAI TTS endpoint
  (`GROQ_TTS_MODEL`, default `playai-tts`; `GROQ_TTS_VOICE`, default
  `Fritz-PlayAI`) and returns raw `audio/mpeg` bytes, played back via a
  plain `new Audio(URL.createObjectURL(blob))` - no extra audio library.
  The text sent is capped at 2000 characters (`app/engine/groq_client.py`)
  to bound cost/latency on a very long reply.
- Both endpoints require authentication (`get_current_user`) but aren't
  tied to a specific chat's ownership - they don't touch chat/document
  data at all, just pass bytes/text through to Groq and back. Both return
  a **503** (`{"error": "groq_not_configured", ...}`) if `GROQ_API_KEY`
  isn't set, and a **502** (`transcription_failed`/`synthesis_failed`) if
  the Groq call itself fails - never a raw 500.
- The frontend hides/disables the mic and speaker buttons when
  `GET /api/config/status`'s `speech` group is `false`, same
  config-missing pattern used for the Azure/SMTP-dependent features
  elsewhere in this app.

> **Groq model deprecations (a moving target - check before you rely on
> the code defaults):** Groq periodically retires model IDs. At the time
> this feature was built and verified, the code defaults
> (`GROQ_LLM_MODEL=llama-3.3-70b-versatile`, `GROQ_TTS_MODEL=playai-tts`)
> had both already been decommissioned server-side (confirmed via a real
> API call, not guessed) - Groq's own `/docs/deprecations` page names
> `openai/gpt-oss-120b` or `qwen/qwen3.6-27b` as chat replacements, and
> `canopylabs/orpheus-v1-english` for TTS (which additionally required
> accepting that model's terms in the Groq console before it would work -
> a one-time step for the account owner, not something this code can do
> for you). Whisper (`GROQ_STT_MODEL=whisper-large-v3`) was still working
> fine. If a speech or chat call fails with a `model_not_found` or
> `model_decommissioned` error, this is almost certainly why - update the
> relevant `GROQ_*_MODEL` value in `.env` to whatever Groq currently
> recommends; nothing in the code needs to change.

### Synchronous ingestion (a deliberate tradeoff)

Ingestion runs **inside** the upload request, synchronously - simplest
thing that works for a portfolio project, and small test documents ingest
in a couple of seconds. A production deployment would instead push
ingestion onto a background worker (Celery, RQ, or arq) backed by a queue
(Redis/SQS/etc.), respond to the upload immediately with
`status=processing`, and let the client poll `GET
/api/chats/{chat_id}/documents` (or a websocket/SSE status channel) until
it flips to `ready`/`failed`. That upgrade is scoped out here deliberately
to keep the moving parts to a minimum while every part that *is* here
still works exactly like it would in a bigger system.

## Repository layout

```
querynest/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, /health, mounts routers
│   │   ├── core/
│   │   │   ├── config.py        # pydantic Settings (env-driven)
│   │   │   ├── security.py      # password hashing + JWT create/verify
│   │   │   └── email.py         # SMTP sending (forgot-password)
│   │   ├── db/
│   │   │   ├── base_class.py    # SQLAlchemy declarative Base
│   │   │   └── session.py       # engine/session, get_db dependency
│   │   ├── models/               # SQLAlchemy models (User, Chat, Message, Document, ...)
│   │   ├── engine/               # AI/RAG engine - zero deps on api/models/auth (see above)
│   │   │   ├── azure_client.py  # Azure OpenAI client construction (embeddings + optional chat)
│   │   │   ├── groq_client.py   # Groq client construction (STT, TTS, optional chat)
│   │   │   ├── llm_provider.py  # LLM_PROVIDER selection (groq default | azure) for chat completions
│   │   │   ├── extraction.py    # file bytes -> [(page_number, text), ...], incl. EasyOCR fallback
│   │   │   ├── chunking.py      # page text -> embedding-sized chunks
│   │   │   ├── qdrant_client.py # vector storage/search - user_id always filtered, chat_id optional
│   │   │   ├── ingestion.py     # extract -> chunk -> embed -> upsert (no DB writes)
│   │   │   └── rag.py           # retrieve() + stream_agentic_reply() (tool-calling agent)
│   │   └── api/
│   │       ├── config_status.py # GET /api/config/status
│   │       ├── auth.py          # /api/auth/* (signup/login/refresh/...)
│   │       ├── chats.py         # /api/chats/* (CRUD, auth-protected)
│   │       ├── documents.py     # /api/chats/{id}/documents/* (upload/list/delete)
│   │       ├── messages.py      # /api/chats/{id}/messages (send + stream RAG answer)
│   │       ├── speech.py        # /api/speech/* (transcribe, synthesize - Groq-backed)
│   │       └── deps.py          # get_current_user dependency
│   ├── alembic/                  # migrations (env.py reads DATABASE_URL from Settings)
│   ├── alembic.ini
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx              # BrowserRouter + App
│   │   ├── App.tsx               # route table
│   │   ├── pages/                # HomePage, Login/Signup/Forgot/Reset, AppShellPage
│   │   ├── components/           # AuthLayout, ProtectedRoute, DocumentUpload
│   │   ├── lib/                  # api client, auth token storage, types, SSE chat stream
│   │   └── index.css
│   ├── .env                       # local dev only (gitignored) - VITE_API_BASE_URL
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Getting started

A step-by-step path from a fresh clone to actually chatting with an
uploaded document:

1. **Clone the repo** and `cd` into it.
2. **Copy the example env file:**
   ```bash
   cp .env.example .env
   ```
3. **Fill in `.env`:**
   - Azure OpenAI embeddings credentials (`AZURE_EM_*`) - always required
     for document upload + chat to work, regardless of chat provider.
   - A chat provider: either leave `LLM_PROVIDER` unset/`groq` (the
     default) and set `GROQ_API_KEY`, or set `LLM_PROVIDER=azure` and fill
     in `LLM_ENDPOINT*`. Without embeddings + whichever chat provider is
     selected, `GET /api/config/status` reports `rag: false` and the
     upload/message endpoints return a 503 instead of crashing (and the
     frontend shows a "Configuration missing" banner and disables the
     chat input).
   - A real `JWT_SECRET_KEY` - generate one with
     `python -c "import secrets; print(secrets.token_hex(32))"` (auth
     won't work without one - see "Authentication & chat history" below).
   - SMTP values are optional, same 503-instead-of-crash pattern.
   - `GROQ_API_KEY` also gates the `speech` group (mic transcription +
     per-message text-to-speech) - optional, same pattern.
   - `.env` is gitignored — never commit it.
4. **Bring the whole stack up:**
   ```bash
   docker-compose up --build
   ```
5. **Run the database migrations** (creates users/chats/messages/
   password_reset_tokens/documents tables - see below):
   ```bash
   docker-compose exec backend alembic upgrade head
   ```
6. **Use the app:**
   - Visit http://localhost:4173, sign up, and you'll land on `/app`.
   - Click **+ New chat**, then drag a `.pdf`/`.docx`/`.txt`/`.jpg`/`.png`
     file onto the upload widget at the top of the chat. Watch its status
     go `processing` → `ready` (or `failed`, with a reason, if something
     went wrong) - a scanned PDF page or an image is OCR'd automatically
     (see "Document types supported" above).
   - Type a question about the document's content into the message box
     at the bottom and hit **Send** - the assistant's answer streams in
     token-by-token, with a `(filename, p.N)`-style citation. Or click the
     mic button to ask by voice instead of typing, and click the speaker
     icon next to any reply to hear it read aloud (see "Speech" above).
   - By default, a question in this chat can be answered from **any**
     document you've uploaded, in any of your chats (see "Document
     retrieval scope" above) - create a second chat, upload a different
     document there, and ask about it from the first chat to see this in
     action. Check **"Only search this chat's documents"** below the
     message box to restrict a question to just the current chat's
     uploads instead.
7. **Other useful URLs:**
   - Backend health check: http://localhost:8000/health
   - Backend config status: http://localhost:8000/api/config/status
   - Backend interactive API docs: http://localhost:8000/docs
   - Qdrant dashboard/API: http://localhost:6333/dashboard

### Running backend/frontend without Docker (local dev)

```bash
# backend
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
alembic upgrade head        # see "Authentication & chat history" below
uvicorn app.main:app --reload

# frontend (separate terminal)
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` to `http://localhost:8000` (see
`frontend/vite.config.ts`), so the frontend can call relative API paths
in dev without setting `VITE_API_BASE_URL` at all. The docker-compose
`frontend` service has no such proxy (it's a static build served by
`serve`, on a different origin/port than the backend), so its image is
built with `VITE_API_BASE_URL` baked in at build time instead - see
`frontend/Dockerfile` and the `VITE_API_BASE_URL` row in "Environment
variables" below.

## Qdrant setup

Qdrant is the vector database used to store and search document
embeddings. In this project it is run as a container alongside everything
else — no separate setup required for local development.

- **Local (default):** `docker-compose up` starts a `qdrant` service from
  the `qdrant/qdrant:latest` image, exposing:
  - `6333` — HTTP/REST API (used by `qdrant-client`)
  - `6334` — gRPC API
  - A named volume (`qdrant_data`) persists collections across restarts.

- **Verify it's running:**
  ```bash
  curl http://localhost:6333/collections
  ```
  A healthy instance returns something like:
  ```json
  {"result":{"collections":[]},"status":"ok","time":0.0}
  ```
  You can also open the built-in dashboard at
  http://localhost:6333/dashboard.

- **Using Qdrant Cloud instead:** if you'd rather not run Qdrant locally
  (e.g. for a deployed demo), provision a free cluster at
  https://cloud.qdrant.io, then:
  1. Set `QDRANT_URL` in `.env` to your cluster URL, e.g.
     `https://xyz-example.eu-central.aws.cloud.qdrant.io:6333`.
  2. Set a `QDRANT_API_KEY` env var - `app/engine/qdrant_client.py` reads
     it (via `os.getenv`, not `app.core.config.Settings` - see the
     "Architecture" section on why the engine package avoids that
     dependency) and passes it to `QdrantClient(api_key=...)`.
  3. You can then remove the `qdrant` service from `docker-compose.yml`
     (or just stop using its port) since the backend will talk to Qdrant
     Cloud over HTTPS instead of the local container.

The collection name defaults to `querynest_documents` (override with
`QDRANT_COLLECTION`), and its vector size comes from `AZURE_EM_DIMENSIONS`
(see the "Environment variables" table below) - `ensure_collection()` in
`app/engine/qdrant_client.py` creates it automatically on first use with
that size, so no manual collection setup is needed either way.

## Environment variables

All variables live in `.env` (gitignored) and are documented with blank
placeholders in `.env.example`. Most of the backend reads them via
`app/core/config.py` (a pydantic `Settings` model) - the exception is
`app/engine/` (Azure OpenAI + Qdrant client construction), which reads
`os.environ` directly rather than importing `Settings`, so that package
has zero dependency on the rest of the app (see "Architecture" above).
Either way, the values all still come from this one `.env` file -
docker-compose's `env_file: .env` on the `backend` service exports every
variable in it as a real process environment variable inside the
container, so nothing needs to be duplicated between the two.

| Variable                          | Purpose                                                              | Required |
|------------------------------------|-----------------------------------------------------------------------|------------------------|
| `DATABASE_URL`                     | Postgres connection string used by SQLAlchemy/Alembic                 | Yes |
| `QDRANT_URL`                       | Base URL of the Qdrant instance (local container or Qdrant Cloud)     | Yes |
| `QDRANT_API_KEY`                   | API key for Qdrant Cloud (leave blank for the local docker-compose container, which has no auth) | Optional |
| `QDRANT_COLLECTION`                | Name of the Qdrant collection documents are stored in                | Optional (defaults to `querynest_documents`) |
| `FRONTEND_URL`                     | Frontend origin - used to build password-reset email links | Yes |
| `VITE_API_BASE_URL`                | Read by docker-compose as a **build arg** for the frontend image (Vite inlines `VITE_*` vars at build time, not at container runtime) - the URL the browser uses to reach the backend | Yes, for the docker-compose `frontend` build |
| `STORAGE_DIR`                       | Where uploaded originals are written on disk, as `{STORAGE_DIR}/{user_id}/{document_id}/original.<ext>` (see `app/api/documents.py`) | Optional (defaults to `storage`, relative to the backend's working directory) |
| `AZURE_EM_ENDPOINT`                 | Azure OpenAI resource endpoint used for embeddings                     | Optional (needed for document upload + chat) |
| `AZURE_EM_API_KEY`                  | API key for the Azure OpenAI embeddings resource                      | Optional |
| `AZURE_EM_API_VERSION`              | Azure OpenAI API version for the embeddings deployment                | Optional |
| `AZURE_EM_MODEL`                    | Azure OpenAI embeddings deployment/model name                         | Optional |
| `AZURE_EM_DIMENSIONS`               | Vector size the embedding deployment returns - sizes the Qdrant collection (`ensure_collection()` in `app/engine/qdrant_client.py`). Confirmed live for this project's deployment: 1536. | Optional (defaults to `1536` in code - `app/engine/azure_client.get_embedding_dimensions()`) |
| `LLM_ENDPOINT`                      | Azure OpenAI resource endpoint used for chat completions - only needed when `LLM_PROVIDER=azure` | Optional |
| `LLM_ENDPOINT_APIKEY`               | API key for the Azure OpenAI chat resource - only needed when `LLM_PROVIDER=azure` | Optional |
| `LLM_MODEL_NAME`                    | Azure OpenAI chat deployment/model name - only needed when `LLM_PROVIDER=azure` | Optional |
| `LLM_ENDPOINT_API_VERSION`          | Azure OpenAI API version for the chat deployment                      | Optional (falls back to `AZURE_EM_API_VERSION`, then a hardcoded default) |
| `LLM_PROVIDER`                      | Which provider serves **chat completions** - `"groq"` or `"azure"` (case-insensitive). Embeddings are always Azure regardless. See "Chat provider selection" above. | Optional (defaults to `groq` if unset/blank/unrecognized) |
| `GROQ_API_KEY`                      | Groq API key - required for the `groq` chat provider AND for both speech endpoints (transcribe/synthesize) | Optional (needed for Groq chat and/or speech) |
| `GROQ_LLM_MODEL`                    | Groq chat-completion model, used when `LLM_PROVIDER=groq`              | Optional (defaults to `llama-3.3-70b-versatile`) |
| `GROQ_STT_MODEL`                    | Groq Whisper model used by `POST /api/speech/transcribe`               | Optional (defaults to `whisper-large-v3`) |
| `GROQ_TTS_MODEL`                    | Groq TTS model used by `POST /api/speech/synthesize`                   | Optional (defaults to `playai-tts`) |
| `GROQ_TTS_VOICE`                    | Groq TTS voice used by `POST /api/speech/synthesize`                   | Optional (defaults to `Fritz-PlayAI`) |
| `JWT_SECRET_KEY`                    | Secret used to sign/verify JWTs - **required** for every auth endpoint (signup/login/refresh/me); generate with `python -c "import secrets; print(secrets.token_hex(32))"` | Yes |
| `JWT_ALGORITHM`                     | JWT signing algorithm                                                  | Yes (defaults to `HS256`) |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`   | Access token lifetime, in minutes                                     | Yes (defaults to `30`) |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS`     | Refresh token lifetime, in days                                        | Yes (defaults to `7`) |
| `SMTP_HOST`                         | SMTP server host, for forgot-password emails                          | Optional - `POST /api/auth/forgot-password` returns 503 (`smtp_not_configured`) until all SMTP vars are set |
| `SMTP_PORT`                         | SMTP server port                                                       | Optional (defaults to `587`) |
| `SMTP_USERNAME`                     | SMTP auth username                                                     | Optional |
| `SMTP_PASSWORD`                     | SMTP auth password                                                     | Optional |
| `SMTP_FROM_EMAIL`                   | "From" address used on outgoing emails                                 | Optional |

`GET /api/config/status`'s `rag` group does **not** check
`AZURE_EM_DIMENSIONS` (unlike the other `AZURE_EM_*` vars) - it has a
sensible code default (`1536`), so a deployment that leaves it blank is
still considered fully configured. See `app/api/config_status.py`'s
`CONFIG_GROUPS`/`_rag_configured()` and
`app/engine/azure_client.get_embedding_dimensions()`. The `rag` group
also does **not** require both chat providers' credentials at once - only
embeddings plus whichever provider `LLM_PROVIDER` currently selects (see
"Chat provider selection" above).

## Authentication & chat history

Phase 2 added email/password authentication (JWT access + refresh tokens)
and Postgres-backed chat/message history, sitting behind
per-user ownership checks that this phase's document retrieval scoping
builds directly on top of (see "Document retrieval scope" above).

### Database migrations (Alembic)

Models live in `backend/app/models/` (`User`, `PasswordResetToken`,
`Chat`, `Message`, `Document`) and are managed by Alembic
(`backend/alembic/`). `alembic/env.py` reads `DATABASE_URL` from the same
`Settings` object the FastAPI app uses (`app/core/config.py`), so there's
one source of truth for the connection string - nothing is duplicated
into `alembic.ini`.

Run migrations against the docker-compose Postgres:

```bash
docker-compose exec backend alembic upgrade head
```

Or from your host machine, outside docker (note the host is `localhost`,
not `postgres` - `postgres` is only resolvable from inside the
docker-compose network; `docker-compose up postgres` still publishes it on
`localhost:5432`):

```bash
cd backend
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/querynest alembic upgrade head
```

To add a migration after changing a model:

```bash
cd backend
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/querynest \
  alembic revision --autogenerate -m "describe the change"
```

### Auth endpoints (`/api/auth/*`)

| Endpoint | Notes |
|---|---|
| `POST /api/auth/signup` | email + password → creates a `User`, returns `{access_token, refresh_token}` |
| `POST /api/auth/login` | verifies password, returns tokens |
| `POST /api/auth/refresh` | exchanges a refresh token for a new access token |
| `GET /api/auth/me` | current user (requires `Authorization: Bearer <access_token>`) |
| `POST /api/auth/forgot-password` | issues a 1-hour `PasswordResetToken` and emails a reset link - **503** (`{"error": "smtp_not_configured", ...}`) if SMTP isn't configured |
| `POST /api/auth/reset-password` | `{token, new_password}` - 400 if the token is invalid/expired/used |

`GET /api/chats`, `POST /api/chats`, `GET /api/chats/{id}`,
`DELETE /api/chats/{id}` all require the same bearer token and 404 (not
403) on a chat that exists but belongs to another user, so ownership
can't be distinguished from non-existence. The document/message endpoints
below follow the exact same pattern.

### Document endpoints (`/api/chats/{chat_id}/documents`)

| Endpoint | Notes |
|---|---|
| `POST /api/chats/{chat_id}/documents` | multipart upload (`file`, one of `.pdf`/`.docx`/`.txt`/`.jpg`/`.jpeg`/`.png` - see "Document types supported" above) - saves the raw bytes, creates a `Document` row (`status=processing`), runs ingestion synchronously, and returns the row with its final `status` (`ready`/`failed`) + `error_message`. **404** if the chat isn't the caller's. **503** (`azure_ai_not_configured` - error code kept for backward compatibility) if the RAG stack (embeddings + the active chat provider) isn't configured. Never a 500 - unsupported file types and ingestion failures land as `status=failed` on the returned row. |
| `GET /api/chats/{chat_id}/documents` | list documents for the chat, newest first |
| `DELETE /api/chats/{chat_id}/documents/{document_id}` | deletes the `Document` row, its Qdrant points, and its `storage/` folder |

### Message endpoints (`/api/chats/{chat_id}/messages`)

| Endpoint | Notes |
|---|---|
| `POST /api/chats/{chat_id}/messages` | `{content, scope}` - `scope` is `"all"` (default, if omitted) or `"chat"`. Persists the user message, then streams the reply from `stream_agentic_reply()` - a tool-calling agent that itself decides whether to call `search_documents` (always scoped to the caller's `user_id`; additionally scoped to this `chat_id` only when `scope="chat"` - both supplied by the endpoint, never the model) before answering - see "Ask a question" above. The reply **streams** back as Server-Sent Events (`event: token` per token, `event: done` at the end, `event: error` on failure) via `StreamingResponse`, and is persisted as an assistant `Message` once the stream completes. **404** if the chat isn't the caller's. **503** if the RAG stack isn't configured (checked *before* the stream starts). |

### Speech endpoints (`/api/speech`)

| Endpoint | Notes |
|---|---|
| `POST /api/speech/transcribe` | multipart upload (`file` - an audio blob, e.g. webm/wav/mp3) - calls Groq's Whisper endpoint and returns `{"text": "..."}`. **503** (`groq_not_configured`) if `GROQ_API_KEY` isn't set. **502** (`transcription_failed`) if the Groq call itself fails. |
| `POST /api/speech/synthesize` | `{"text": "..."}` - calls Groq's PlayAI TTS endpoint and returns raw `audio/mpeg` bytes. **503** (`groq_not_configured`)/**502** (`synthesis_failed`), same pattern. **400** if `text` is empty. |

Both speech endpoints require authentication but aren't scoped to a
specific chat - see "Speech" above.

### Manual test flow

```bash
# 1. sign up
curl -X POST http://localhost:8000/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-long-password"}'
# -> {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}

# 2. log in (same credentials)
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-long-password"}'

# 3. create a chat (use the access_token from step 1 or 2)
curl -X POST http://localhost:8000/api/chats \
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" -d '{}'
# -> {"id": 1, "title": "New chat", ...}

# 4. upload a document into that chat
curl -X POST http://localhost:8000/api/chats/1/documents \
  -H "Authorization: Bearer <access_token>" -F "file=@/path/to/your.pdf"
# -> {"id": 1, "filename": "your.pdf", "status": "ready", "error_message": null, ...}

# 5. ask a question about it - the answer streams back as SSE
# (scope defaults to "all" when omitted - this searches every chat this
# user owns, not just chat 1; add "scope":"chat" to restrict to chat 1 only)
curl -N -X POST http://localhost:8000/api/chats/1/messages \
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" \
  -d '{"content":"What does this document say about X?"}'
```

Or through the UI: visit http://localhost:4173, sign up, land on `/app`,
click **+ New chat**, drag a file onto the upload widget, then type a
question in the message box - the reply streams in live.

The backend exposes `GET /api/config/status` which reports, per group,
whether every variable in that group is set, plus which chat provider is
currently active:

```json
{ "rag": true, "smtp": true, "speech": true, "llm_provider": "groq" }
```

The frontend uses this to show "configuration missing" banners for
features that depend on secrets which haven't been provided yet - when
`rag` is `false`, the chat input is disabled entirely with a
"Configuration missing" banner, and when `speech` is `false` the mic/
speaker buttons are hidden (see `AppShellPage.tsx`).

### Verifying retrieval scope yourself

This is the property the whole feature depends on, so it's worth checking
directly rather than trusting the code - `backend/tests/test_qdrant_isolation.py`
now exercises exactly this against a real (disposable) Qdrant collection
as part of the automated suite (see "Running tests" below), but the manual
walkthrough is still worth doing once yourself against the live app. There
are two things to prove: default (`scope="all"`) retrieval correctly
reaches across a user's own chats, and both modes correctly refuse to
cross into another user's data.

1. **Default scope reaches across your own chats:** create chat A (no
   documents) and chat B (upload a document to it), then ask chat A a
   question that can only be answered from chat B's document, with
   `scope` omitted/`"all"`. The answer should come back correctly
   grounded in chat B's document, with a citation - this is the intended
   behavior now, not a leak.
2. **Chat-scoped mode actually restricts:** ask that same question in
   chat A again, this time with `"scope":"chat"`. The answer should now
   say the excerpts don't contain that information, since chat A has no
   documents of its own.
3. **Cross-user isolation holds in both modes:** log in as a different
   user (who has uploaded nothing) and ask the same question, with scope
   omitted (`"all"`). The answer must still come back empty - the
   `user_id` filter is unconditional regardless of scope, so one user's
   documents are never reachable by another no matter which mode is used.
4. For a lower-level check on any of the above, call
   `app/engine/rag.retrieve()` directly (a plain function, no HTTP
   needed) with the relevant `user_id`/`chat_id` combination and inspect
   the returned chunks - this proves the Qdrant `must` filter itself is
   doing the work, not just that the LLM chose to follow its prompt
   instructions.

## Running tests

`backend/tests/` holds the automated suite (96 tests) that replaced the
purely-manual verification this project relied on through Phase 3:

| File | Covers |
|---|---|
| `test_qdrant_isolation.py` | **The most important test file in this project** - `engine/qdrant_client.py`'s `search()` isolation boundary, against a **real** (disposable, uniquely-named) Qdrant collection: a `user_id` mismatch returns nothing regardless of scope (including when `chat_id` *does* match, and when two different users happen to reuse the same numeric `chat_id`), default scope (`chat_id=None`) spans every chat a user owns, an explicit `chat_id` restricts to just that chat even when the query embedding is a closer match to another chat's document, and `delete_document()` only removes the targeted document's points. |
| `test_chunking.py` | `engine/chunking.py` boundary cases - empty/whitespace-only pages, a page just under/at/over the split threshold, a page needing several chunks, a hard character-cut when a single paragraph has no boundary to split on, and `page_number`/`chunk_index` bookkeeping across multiple pages. |
| `test_extraction.py` | `engine/extraction.py` against real fixture files (`tests/fixtures/sample.pdf`, `sample.txt`) - per-page PDF text via `pdfplumber`, TXT-as-a-single-page, invalid-UTF-8 handling, `UnsupportedFileTypeError` for an unrecognized/missing extension, and the **OCR fallback**: a page with real embedded text never invokes OCR, a page with empty/near-empty text triggers the EasyOCR/pymupdf fallback (both mocked - real OCR inference is far too slow for the automated suite), a mixed real-text/scanned document is decided per-page, and an image file is OCR'd directly. |
| `test_llm_provider.py` | `engine/llm_provider.py`'s `LLM_PROVIDER` selection - defaults to `"groq"` for unset/blank/unrecognized values, case-insensitive `"azure"` detection, `chat_provider_configured()`/`get_active_chat_provider()` check/construct only the selected provider's client (never the other one's), with azure_client/groq_client's real client construction monkeypatched. |
| `test_rag.py` | `engine/rag.py`'s agentic tool-calling pipeline, `stream_agentic_reply()` - a direct answer (greeting, no tool call) never invokes `retrieve()` or a second completion; prior chat history is included in the first call; a tool-call round trip calls `retrieve()` with exactly the query/user_id/chat_id expected (proving the model can only ever supply the query string, never user_id/chat_id) and makes a second streamed call carrying a `role="tool"` message; a no-matching-chunks tool result still produces a final streamed answer. With the active chat provider's client mocked throughout (no real Groq/Azure calls). |
| `test_config_status.py` | `GET /api/config/status`'s provider-aware `rag` group - `true` only when embeddings AND the *currently selected* provider's own vars are set (never both providers' credentials at once), plus the `speech` group and the `llm_provider` field. |
| `test_auth_api.py` | `/api/auth/*` integration tests via FastAPI's `TestClient` - signup, duplicate email, login, wrong password, refresh-token type-confusion, and the `jwt_not_configured`/`smtp_not_configured` 503 gates with those env vars unset. |
| `test_chats_api.py` | `/api/chats/*` ownership checks - a chat belonging to another user 404s (indistinguishable from one that never existed), scoped listing, delete. |
| `test_documents_api.py` | `/api/chats/{id}/documents/*` ownership checks plus the upload → status transition (`processing` → `ready`/`failed`), with `azure_ai_configured`/`ingest_document` monkeypatched to deterministic fakes so the test targets the endpoint's own status-transition logic rather than making a real Azure OpenAI/Groq call. |
| `test_messages_api.py` | `/api/chats/{id}/messages` - `stream_agentic_reply()` (mocked to a fake deterministic async generator) is the only engine call this endpoint makes; asserts the authenticated `user_id` (never anything from the request body) and the scope-derived `chat_id` are what's passed through, plus auto-titling and the 503/404 gates. |
| `test_speech_api.py` | `/api/speech/*` - 503 when Groq isn't configured, auth-required, happy-path wiring with `transcribe_audio`/`synthesize_speech` monkeypatched to deterministic fakes, and Groq failures surfacing as a 502 rather than a raw 500. |

**Test database choice:** the integration tests (`test_auth_api.py`,
`test_chats_api.py`, `test_documents_api.py`, `test_messages_api.py`,
`test_speech_api.py`, `test_config_status.py`) run against an in-memory
SQLite database via a `get_db` dependency override, not the docker-compose
Postgres instance - documented in full in `backend/tests/conftest.py`'s
module docstring. Short version: nothing these tests exercise depends on
Postgres-specific behavior (no raw SQL, no JSONB/array columns, cascades
are ORM-level `relationship(cascade=...)`, not DB triggers), so a fresh
zero-setup SQLite schema per test is the simpler and equally correct
choice - it is not a substitute for `alembic upgrade head` against real
Postgres, which is exercised by actually running this project via
docker-compose. `test_qdrant_isolation.py` is different: since Qdrant's
filtering behavior is the single property this whole project depends on,
those tests run against a real Qdrant instance rather than a mock.

**Run the suite** inside the backend container (simplest - reuses the
already-running docker-compose stack's network, so `test_qdrant_isolation.py`
reaches Qdrant at its docker-compose service name with no extra
configuration):

```bash
docker-compose exec backend pytest tests/ -v
```

Or from the host, outside docker (point `QDRANT_TEST_URL` at the
docker-compose-published port, since `qdrant` as a hostname only resolves
inside the docker network):

```bash
cd backend
pip install -r requirements.txt
QDRANT_TEST_URL=http://localhost:6333 pytest tests/ -v
```

Expected output ends with something like:

```
======================== 96 passed, 7 warnings in ~20s ========================
```

(The warnings are pytest-asyncio/passlib deprecation notices unrelated to
this project's code - not failures.)

**A note on image size/build time:** adding EasyOCR (which pulls in
CPU-only PyTorch/torchvision) and pymupdf to `backend/requirements.txt`
makes the backend Docker image noticeably larger and slower to build than
before - observed for this project: the `backend` image grew to
**~3.1 GB** (from a few hundred MB), and a from-scratch
`docker-compose build backend` took **~6m20s** (dominated by downloading
the CPU-only PyTorch/torchvision wheels and EasyOCR's other dependencies -
mostly network time, not CPU). Re-run
`docker-compose build backend` and compare `docker images` if you want to
confirm this on your own machine.

## Roadmap

- **Phase 1:** repo scaffold, docker-compose, health/config endpoints.
- **Phase 2:** database models + Alembic migrations, authentication (JWT +
  forgot-password via SMTP), chat/message history CRUD, and the frontend
  auth + chat-shell pages.
- **Phase 3:** the `app/engine/` RAG package (extraction, chunking, Azure
  OpenAI embeddings/chat, Qdrant storage/search with per-user isolation
  and an opt-in per-chat retrieval scope), document upload/list/delete
  endpoints, a streaming (SSE) message-send endpoint, and the frontend
  upload widget + streaming chat input with a chat-scope checkbox.
- **Phase 4:** the automated test suite, real screenshots of the running
  app in this README, and a final documentation pass (this file).
- **Phase 5:** OCR for images and scanned PDF pages (EasyOCR + pymupdf,
  per-page fallback), speech (Groq Whisper transcription via a mic
  button, Groq PlayAI text-to-speech per message), and a selectable chat
  provider (`LLM_PROVIDER` - Groq by default, or Azure OpenAI).
- **Phase 6 (this phase):** replaced all fixed Python branching between
  canned system prompts (a DB "has any ready document" gate choosing
  between a grounded prompt, a general-knowledge-fallback prompt, and a
  separate "onboarding" prompt) with a single genuinely agentic,
  tool-calling design - `stream_agentic_reply()` gives the model one real
  tool (`search_documents`) and lets it decide, per message, whether the
  question needs retrieval at all, including greeting/small-talk/
  "what is QueryNest" questions that are now answered directly rather than
  via any hardcoded detection. This fixed a real bug found through live
  testing: a user with documents sitting in some other chat saying "hi"
  previously got an awkward grounded-refusal instead of a natural
  greeting, because the old design branched only on "does this user have
  any ready document anywhere," never on what the message actually asked.
- **Phase 7 (not started):** production-shaped upgrades called out as
  deliberate tradeoffs above - a background task queue for ingestion
  instead of synchronous processing, and deployment configuration (this
  project targets local docker-compose only; a real deployment would also
  need CORS tightened from `allow_origins=["*"]`, HTTPS termination, and a
  secrets manager instead of a `.env` file).
