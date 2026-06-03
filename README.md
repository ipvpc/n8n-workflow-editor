# n8n Workflow Editor

Dockerized web UI to list and edit **remote** n8n workflows via the official REST API, with an AI assistant that understands n8n workflow JSON.

## Run (Docker Compose, repo root)

```bash
docker compose up --build n8n-workflow-editor
```

Open `http://localhost:8105` (or set `N8N_WORKFLOW_EDITOR_PORT`).

The app runs in **multi-instance mode**: n8n connections and LLM profiles are stored in PostgreSQL. Use the **Connections** page in the UI to add n8n instances.

### Workflow sync and backups

Workflows are cached locally in PostgreSQL for the active n8n instance:

1. **Sync from n8n** — pull all workflows from the remote instance into the local cache (skips workflows with unsaved local changes).
2. **Edit** — change workflow JSON in the editor.
3. **Save locally** — store changes in the local cache (marked as modified).
4. **Push to n8n** — send the local copy to the remote n8n instance (creates an automatic pre-push backup).
5. **Pull from n8n** — overwrite the local copy with the remote version.
6. **Backup / Restore** — create named snapshots and restore them locally (optionally push after restore).

Per-workflow API (selected):

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/workflows/local` | List cached workflows |
| GET | `/api/workflows/local/{id}` | Get cached workflow JSON |
| PUT | `/api/workflows/local/{id}` | Save local edits |
| POST | `/api/workflows/sync` | Sync all from n8n |
| POST | `/api/workflows/local/{id}/pull` | Pull one from n8n |
| POST | `/api/workflows/local/{id}/push` | Push one to n8n |
| POST | `/api/workflows/local/{id}/backups` | Create backup |
| GET | `/api/workflows/local/{id}/backups` | List backups |
| POST | `/api/workflows/local/{id}/backups/{backup_id}/restore` | Restore backup |

## Configuration

### Database (required)

- `DATABASE_URL` — PostgreSQL connection string (included in Docker Compose via `.env`).
- `POSTGRES_PASSWORD` — Postgres password for the bundled `postgres` service (default `n8n_editor`).

On first startup, if `N8N_BASE_URL` and `N8N_API_KEY` are set in the environment, a default n8n instance and LLM profile are bootstrapped into the database.

### API authentication and runtime safety

- `N8N_EDITOR_REQUIRE_AUTH` (default `false` in development) — when `true`, all `/api/*` routes require `Authorization: Bearer <token>`.
- `N8N_EDITOR_AUTH_TOKEN` — required when API auth is enabled.
- `N8N_WORKFLOW_EDITOR_ENV` — set to `production` to enable stricter safety checks.
- `N8N_ALLOW_PRIVATE_NETWORK_TARGETS` — allow LAN/private n8n URLs (e.g. `192.168.x.x`). Defaults to **allowed** in development, **blocked** in production unless set to `true`.
- Browser UI reads the bearer token from `localStorage["n8n_editor_auth_token"]` and sends it automatically.

### n8n connection

Add instances on the **Connections** page (name, base URL, API key). One instance is **active** at a time for the workflow list, editor, and chat.

Optional env bootstrap on first run:

- `N8N_BASE_URL` — Root URL of your n8n instance, e.g. `https://n8n.example.com`
- `N8N_API_KEY` — API key (`X-N8N-API-KEY`). Create under **Settings → n8n API** in n8n.

Per-instance options (set in the UI when adding an instance):

- `N8N_HTTP_TIMEOUT_SECONDS` (default `60`)
- `N8N_SKIP_TLS_VERIFY` — If `true`, disables TLS verification for n8n HTTPS calls. **Unsafe**; for lab use only (default `false`).
  In production mode, this must remain `false`.

### AI (chat)

Configure LLM profiles on the **Settings** page (stored in PostgreSQL). On first startup, env vars can bootstrap a default profile:

**Azure OpenAI**

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT`
- `AZURE_OPENAI_API_VERSION` (default `2024-08-01-preview`)

**OpenAI / compatible**

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` (default `https://api.openai.com/v1`)
- `OPENAI_MODEL` (default `gpt-4o-mini`)

Optional tuning:

- `N8N_EDITOR_AI_TEMPERATURE` (default `0.2`)
- `N8N_EDITOR_AI_MAX_TOKENS` (default `4096`)

Health: `GET /api/ai/status` — reports whether AI env is complete.

## Build UI without Docker

From `n8n-workflow-editor/frontend`:

```bash
npm install
npm run build
```

This writes the production bundle to `n8n-workflow-editor/app/static` for local `uvicorn` runs.

## API (selected)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness |
| GET | `/api/settings/n8n` | Masked connection info |
| PUT | `/api/settings/n8n` | Save connection to `/data` |
| DELETE | `/api/settings/n8n` | Remove saved file |
| POST | `/api/n8n/test` | Test n8n API |
| GET | `/api/workflows` | List workflows |
| GET | `/api/workflows/{id}` | Get workflow |
| PATCH | `/api/workflows/{id}` | Update workflow |
| POST | `/api/chat` | AI chat (optional tools) |

## Security

- API keys are kept on the server; the UI shows masked values only.
- Run behind HTTPS and restrict network access in production; this app does not implement multi-user auth.
