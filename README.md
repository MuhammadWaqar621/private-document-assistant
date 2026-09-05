# querynest

A RAG-powered document chat assistant. Upload documents, and ask questions
answered strictly from their content, with retrieved passages backing every
response. Built as a portfolio project to demonstrate a production-shaped
retrieval-augmented-generation stack: chunking and embedding documents,
storing vectors in a purpose-built vector database, and grounding an LLM's
answers in retrieved context rather than letting it hallucinate freely.

> **Status:** Phase 2 — authentication (email/password + Google OAuth) and
> chat/message history are implemented on top of the Phase 1 scaffold
> (FastAPI backend, React frontend, Postgres, and Qdrant wired together via
> docker-compose). Document ingestion and the actual chat/retrieval (RAG)
> pipeline - i.e. sending a message and getting an AI answer back - are
> built in a later phase.

## Architecture

```
                          ┌──────────────────┐
                          │   Frontend        │
                          │   React + Vite    │
                          │   + TypeScript    │
                          │   + Tailwind      │
                          └────────┬──────────┘
                                   │ HTTP (/api/*)
                                   ▼
                          ┌──────────────────┐
                          │   Backend         │
                          │   FastAPI         │
                          │   SQLAlchemy      │
                          │   + Alembic       │
                          └───┬──────────┬────┘
                              │          │
                 ┌────────────┘          └────────────┐
                 ▼                                     ▼
        ┌──────────────────┐                 ┌──────────────────┐
        │   Postgres        │                 │   Qdrant          │
        │   (relational     │                 │   (vector store   │
        │   data: users,    │                 │   for document    │
        │   documents,      │                 │   embeddings)     │
        │   chat history)   │                 │                   │
        └──────────────────┘                 └──────────────────┘

        Backend also talks to Azure OpenAI (via the Azure OpenAI client
        in the `openai` SDK) for:
          - text embeddings  (AZURE_EM_* settings)
          - chat completions (LLM_ENDPOINT_MINI_MODEL* settings)
```

- **Backend** — Python 3.11+, FastAPI, SQLAlchemy + Alembic for migrations,
  Postgres for relational data (users, documents, chat sessions), Qdrant
  for vector search. AI calls go through `openai.AzureOpenAI` /
  `AsyncAzureOpenAI` — this project targets **Azure OpenAI** specifically,
  not the public OpenAI API.
- **Frontend** — React + TypeScript + Vite + Tailwind CSS.
- **Vector DB** — Qdrant, run locally via docker-compose (or pointed at
  Qdrant Cloud — see below).
- **Auth** — email/password (JWT access + refresh tokens, `passlib`/bcrypt
  hashing) and "Sign in with Google" (`authlib`), plus forgot/reset
  password via SMTP (`aiosmtplib`). See "Authentication & chat history"
  below.

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
│   │   ├── models/               # SQLAlchemy models (User, Chat, Message, ...)
│   │   └── api/
│   │       ├── config_status.py # GET /api/config/status
│   │       ├── auth.py          # /api/auth/* (signup/login/refresh/...)
│   │       ├── chats.py         # /api/chats/* (CRUD, auth-protected)
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
│   │   ├── components/           # AuthLayout, GoogleSignInButton, ProtectedRoute
│   │   ├── lib/                  # api client, auth token storage, types
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

1. Copy the example env file:
   ```bash
   cp .env.example .env
   ```
2. Fill in your Azure OpenAI credentials, and generate a real
   `JWT_SECRET_KEY` (auth won't work without one - see "Authentication &
   chat history" below). Google OAuth / SMTP values are optional; the
   features that need them return a clear 503 instead of crashing when
   they're blank. `.env` is gitignored — never commit it.
3. Bring the whole stack up:
   ```bash
   docker-compose up --build
   ```
4. Run the database migrations (creates users/chats/messages/
   password_reset_tokens tables - see below):
   ```bash
   docker-compose exec backend alembic upgrade head
   ```
5. Visit:
   - Frontend: http://localhost:4173
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
  2. Add a `QDRANT_API_KEY` env var (the backend's Qdrant client should be
     initialized with `api_key=settings.QDRANT_API_KEY` once the
     ingestion/retrieval code lands — that variable isn't in scope for
     this scaffold phase, but the setup is documented here since it's the
     most common follow-up question).
  3. You can then remove the `qdrant` service from `docker-compose.yml`
     (or just stop using its port) since the backend will talk to Qdrant
     Cloud over HTTPS instead of the local container.

## Environment variables

All variables live in `.env` (gitignored) and are documented with blank
placeholders in `.env.example`. The backend reads them via
`app/core/config.py` (a pydantic `Settings` model).

| Variable                          | Purpose                                                              | Required |
|------------------------------------|-----------------------------------------------------------------------|------------------------|
| `DATABASE_URL`                     | Postgres connection string used by SQLAlchemy/Alembic                 | Yes |
| `QDRANT_URL`                       | Base URL of the Qdrant instance (local container or Qdrant Cloud)     | Yes |
| `FRONTEND_URL`                     | Frontend origin - used to build password-reset email links and the Google OAuth redirect back into the app | Yes |
| `VITE_API_BASE_URL`                | Read by docker-compose as a **build arg** for the frontend image (Vite inlines `VITE_*` vars at build time, not at container runtime) - the URL the browser uses to reach the backend | Yes, for the docker-compose `frontend` build |
| `AZURE_EM_ENDPOINT`                 | Azure OpenAI resource endpoint used for embeddings                     | Optional (needed for AI features, later phase) |
| `AZURE_EM_API_KEY`                  | API key for the Azure OpenAI embeddings resource                      | Optional |
| `AZURE_EM_API_VERSION`              | Azure OpenAI API version for the embeddings deployment                | Optional |
| `AZURE_EM_MODEL`                    | Azure OpenAI embeddings deployment/model name                         | Optional |
| `LLM_ENDPOINT_MINI_MODEL`           | Azure OpenAI resource endpoint used for chat completions               | Optional |
| `LLM_ENDPOINT_MINI_MODEL_APIKEY`    | API key for the Azure OpenAI chat resource                            | Optional |
| `MINI_MODEL_NAME`                   | Azure OpenAI chat deployment/model name (e.g. a "mini" model)          | Optional |
| `JWT_SECRET_KEY`                    | Secret used to sign/verify JWTs - **required** for every auth endpoint (signup/login/refresh/me); generate with `python -c "import secrets; print(secrets.token_hex(32))"` | Yes |
| `JWT_ALGORITHM`                     | JWT signing algorithm                                                  | Yes (defaults to `HS256`) |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`   | Access token lifetime, in minutes                                     | Yes (defaults to `30`) |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS`     | Refresh token lifetime, in days                                        | Yes (defaults to `7`) |
| `GOOGLE_CLIENT_ID`                  | Google OAuth client ID (for "Sign in with Google")                    | Optional - `GET /api/auth/google/*` return 503 until both Google vars are set |
| `GOOGLE_CLIENT_SECRET`              | Google OAuth client secret                                             | Optional |
| `SMTP_HOST`                         | SMTP server host, for forgot-password emails                          | Optional - `POST /api/auth/forgot-password` returns 503 (`smtp_not_configured`) until all SMTP vars are set |
| `SMTP_PORT`                         | SMTP server port                                                       | Optional (defaults to `587`) |
| `SMTP_USERNAME`                     | SMTP auth username                                                     | Optional |
| `SMTP_PASSWORD`                     | SMTP auth password                                                     | Optional |
| `SMTP_FROM_EMAIL`                   | "From" address used on outgoing emails                                 | Optional |

## Authentication & chat history

Phase 2 adds email/password + Google OAuth authentication (JWT access +
refresh tokens) and Postgres-backed chat/message history, sitting behind
per-user ownership checks that the later RAG phase's per-user document
isolation builds directly on top of.

### Database migrations (Alembic)

Models live in `backend/app/models/` (`User`, `PasswordResetToken`,
`Chat`, `Message`) and are managed by Alembic (`backend/alembic/`).
`alembic/env.py` reads `DATABASE_URL` from the same `Settings` object the
FastAPI app uses (`app/core/config.py`), so there's one source of truth
for the connection string - nothing is duplicated into `alembic.ini`.

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
| `GET /api/auth/google/login` | redirects to Google's consent screen - **503** if `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` aren't set |
| `GET /api/auth/google/callback` | exchanges the code, finds-or-creates the `User` by `google_id`/email, then **redirects** to `{FRONTEND_URL}/app?access_token=...&refresh_token=...` (simplest correct approach for a portfolio project - tokens are consumed once from the URL by `AppShellPage` and immediately stripped from the address bar; a production app would likely use a short-lived one-time code instead) |

`GET /api/chats`, `POST /api/chats`, `GET /api/chats/{id}`,
`DELETE /api/chats/{id}` all require the same bearer token and 404 (not
403) on a chat that exists but belongs to another user, so ownership
can't be distinguished from non-existence.

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

# 4. list chats - the one just created should be there
curl http://localhost:8000/api/chats -H "Authorization: Bearer <access_token>"
```

Or through the UI: visit http://localhost:4173, click **Sign up**, then
you'll land on `/app` where "+ New chat" and the sidebar chat list are
wired to the same endpoints. Sending a message isn't implemented yet by
design - that's the next phase.

The backend exposes `GET /api/config/status` which reports, per group,
whether every variable in that group is set:

```json
{ "azure_ai": true, "google_oauth": false, "smtp": false }
```

The frontend uses this to show "configuration missing" banners for
features that depend on secrets which haven't been provided yet.

## Roadmap

- **Phase 1:** repo scaffold, docker-compose, health/config endpoints.
- **Phase 2 (this phase):** database models + Alembic migrations,
  authentication (JWT + Google OAuth + forgot-password via SMTP),
  chat/message history CRUD, and the frontend auth + chat-shell pages.
- **Phase 3:** document upload + storage, embedding/ingestion pipeline
  into Qdrant.
- **Phase 4:** retrieval + streaming chat endpoint (the actual RAG logic,
  wired into the existing chat/message models), per-user document scoping.
- **Phase 5:** frontend chat UI polish (streaming responses, citations),
  deployment.
