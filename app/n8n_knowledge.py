"""Curated n8n reference for the AI system prompt (deterministic, offline)."""

N8N_KNOWLEDGE_PACK = """
## n8n workflow JSON (Public API)

- Workflows are JSON objects. Common top-level fields from the API: `id`, `name`, `active`, `nodes`, `connections`, `settings`, `staticData`, `meta`, `pinData`, `tags` (structure depends on n8n version).
- Each **node** typically has: `id` (UUID string), `name`, `type` (e.g. `n8n-nodes-base.httpRequest`), `typeVersion` (number), `position` [x, y], `parameters` (object), optional `credentials`, `disabled`, `notes`, `retryOnFail`, `maxTries`.
- **connections** map source node names to outputs → target nodes: `{ "Source Node Name": { "main": [[{ "node": "Target", "type": "main", "index": 0 }]] } }`.
- **Expressions**: in many parameter fields you can use `={{ ... }}` with n8n expression syntax to read from `$json`, `$node["Node Name"].json`, etc.
- **Pin data**: `pinData` holds pinned execution data for debugging; avoid baking secrets into pinned data when exporting.
- **Credentials**: reference credential IDs configured in the n8n instance; do not invent credential IDs—use placeholders and tell the user to attach credentials in the n8n UI.
- **Best practices**: keep node names unique and stable; prefer explicit error workflows where appropriate; document external API rate limits in notes; use Code node only when necessary and keep it small and testable.
- **HTTP Request node**: common `parameters` include `method`, `url`, `authentication`, `sendBody`, `specifyBody`, `jsonBody` (often as expression).
- **Webhooks**: webhook nodes expose paths; changing workflow active state affects whether triggers listen.
- **Validation**: after editing JSON, ensure `connections` reference existing `nodes[].name` values and indices exist.

## REST API usage (reminder)

- List: GET `/api/v1/workflows`
- Get: GET `/api/v1/workflows/{id}`
- Update: PATCH `/api/v1/workflows/{id}` with partial body
- Header: `X-N8N-API-KEY`

When helping the user, prefer small PATCH-style edits and explain which nodes and connections change. If unsure about a node type's parameters, say so and suggest verifying in the n8n node panel.
"""
