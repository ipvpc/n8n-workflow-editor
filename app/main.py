"""n8n Workflow Editor: FastAPI app."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import settings_store
from .ai_chat import ChatRequest, ai_status, run_chat
from .n8n_client import N8nClientError, client_from_resolved

logging.basicConfig(level=os.environ.get("N8N_WORKFLOW_EDITOR_LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_STATIC = os.path.join(os.path.dirname(__file__), "static")
_ASSETS = os.path.join(_STATIC, "assets")

app = FastAPI(title="n8n Workflow Editor", version="0.1.0")

if os.path.isdir(_ASSETS):
    app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")


def _get_client():
    base, key = settings_store.resolved_connection()
    try:
        return client_from_resolved(base, key)
    except N8nClientError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "n8n-workflow-editor"}


class N8nSettingsPublic(BaseModel):
    base_url: str | None = None
    api_key_masked: str | None = None
    has_api_key: bool = False
    source: str = "env"


@app.get("/api/settings/n8n", response_model=N8nSettingsPublic)
def get_n8n_settings():
    f = settings_store.load_settings()
    base_env = settings_store._env_base_url()  # noqa: SLF001
    key_env = settings_store._env_api_key()  # noqa: SLF001

    if f:
        pub = f.model_dump_public()
        return N8nSettingsPublic(
            base_url=pub.get("base_url") or base_env or None,
            api_key_masked=pub.get("api_key_masked"),
            has_api_key=bool(f.api_key) or bool(key_env),
            source="file" if f.base_url or f.api_key else "env",
        )
    return N8nSettingsPublic(
        base_url=base_env or None,
        api_key_masked=settings_store.mask_api_key(key_env) if key_env else None,
        has_api_key=bool(key_env),
        source="env",
    )


class PutN8nSettingsBody(BaseModel):
    base_url: str = Field(..., description="n8n instance root URL")
    api_key: str | None = Field(None, description="If omitted or empty, keep existing saved key")


@app.put("/api/settings/n8n")
def put_n8n_settings(body: PutN8nSettingsBody):
    try:
        base = settings_store.validate_base_url(body.base_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    prev = settings_store.load_settings()
    new_key = (body.api_key or "").strip()
    if new_key:
        api_key = new_key
    elif prev and prev.api_key:
        api_key = prev.api_key
    else:
        api_key = settings_store._env_api_key()  # noqa: SLF001

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="api_key is required (or set N8N_API_KEY in environment) when none is stored",
        )

    st = settings_store.N8nConnectionSettings(base_url=base, api_key=api_key)
    settings_store.save_settings(st)
    pub = st.model_dump_public()
    return {"ok": True, **pub, "source": "file"}


@app.delete("/api/settings/n8n")
def delete_n8n_settings():
    removed = settings_store.delete_settings_file()
    return {"ok": True, "removed_file": removed}


@app.post("/api/n8n/test")
async def test_n8n():
    try:
        c = _get_client()
        data = await c.health_ping()
        return {"ok": True, "sample": data}
    except N8nClientError as e:
        raise HTTPException(status_code=502, detail={"message": str(e), "body": e.body}) from e


@app.get("/api/workflows")
async def api_list_workflows(
    active: bool | None = None,
    limit: int | None = None,
    cursor: str | None = None,
):
    try:
        c = _get_client()
        return await c.list_workflows(active=active, limit=limit, cursor=cursor)
    except N8nClientError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail={"message": str(e), "body": e.body},
        ) from e


@app.get("/api/workflows/{workflow_id}")
async def api_get_workflow(workflow_id: str):
    try:
        c = _get_client()
        return await c.get_workflow(workflow_id)
    except N8nClientError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail={"message": str(e), "body": e.body},
        ) from e


@app.post("/api/workflows")
async def api_create_workflow(body: dict[str, Any]):
    try:
        c = _get_client()
        return await c.create_workflow(body)
    except N8nClientError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail={"message": str(e), "body": e.body},
        ) from e


@app.patch("/api/workflows/{workflow_id}")
async def api_patch_workflow(workflow_id: str, body: dict[str, Any]):
    try:
        c = _get_client()
        return await c.update_workflow(workflow_id, body)
    except N8nClientError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail={"message": str(e), "body": e.body},
        ) from e


@app.delete("/api/workflows/{workflow_id}")
async def api_delete_workflow(workflow_id: str):
    try:
        c = _get_client()
        return await c.delete_workflow(workflow_id)
    except N8nClientError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail={"message": str(e), "body": e.body},
        ) from e


@app.get("/api/ai/status")
def api_ai_status():
    return ai_status()


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    try:
        return await run_chat(req)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/")
def index():
    index_path = os.path.join(_STATIC, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=503, detail="static UI missing")
    return FileResponse(index_path)


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    """Serve SPA for client routes."""
    if full_path.startswith("api"):
        raise HTTPException(status_code=404, detail="not found")
    index_path = os.path.join(_STATIC, "index.html")
    if not os.path.isfile(index_path):
        raise HTTPException(status_code=503, detail="static UI missing")
    return FileResponse(index_path)
