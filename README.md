# n8n Workflow Editor

Dockerized web UI to list and edit **remote** n8n workflows via the official REST API, with an AI assistant that understands n8n workflow JSON.

## Run (Docker Compose, repo root)

```bash
docker compose up --build n8n-workflow-editor
```

Open `http://localhost:8105` (or set `N8N_WORKFLOW_EDITOR_PORT`).

Persisted settings (optional UI overrides for env defaults) live in the named volume `n8n_workflow_editor_data` mounted at `/data` as `n8n-connection.json`.

## Configuration

### n8n connection

- `N8N_BASE_URL` — Root URL of your n8n instance, e.g. `https://n8n.example.com` (no trailing slash required).
- `N8N_API_KEY` — API key (`X-N8N-API-KEY`). Create under **Settings → n8n API** in n8n.

You can also set connection details in the **Settings** dialog in the UI; saved values override env for the container and are stored under `/data`.

- `N8N_HTTP_TIMEOUT_SECONDS` (default `60`)
- `N8N_SKIP_TLS_VERIFY` — If `true`, disables TLS verification for n8n HTTPS calls. **Unsafe**; for lab use only (default `false`).

### AI (chat)

Configure **either** Azure OpenAI **or** OpenAI-compatible API:

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
