"""n8n Workflow Editor: FastAPI app."""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import database, multi_config, settings_store, workflow_sync
from .ai_chat import ChatRequest, ai_status, run_chat
from .n8n_client import N8nClientError, client_from_resolved, close_shared_clients

logging.basicConfig(level=os.environ.get("N8N_WORKFLOW_EDITOR_LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_STATIC = os.path.join(os.path.dirname(__file__), "static")
_ASSETS = os.path.join(_STATIC, "assets")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _validate_security_configuration()
    await database.init_db()
    yield
    await close_shared_clients()
    await database.close_db()


app = FastAPI(title="n8n Workflow Editor", version="0.1.0", lifespan=lifespan)

if os.path.isdir(_ASSETS):
    app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes")


def _is_production() -> bool:
    return os.environ.get("N8N_WORKFLOW_EDITOR_ENV", "").strip().lower() in ("prod", "production")


def _api_auth_required() -> bool:
    # Keep local dev friction low, but enforce auth by default in production.
    return _bool_env("N8N_EDITOR_REQUIRE_AUTH", default=_is_production())


def _expected_api_token() -> str:
    return os.environ.get("N8N_EDITOR_AUTH_TOKEN", "").strip()


def _validate_security_configuration() -> None:
    if _is_production() and _bool_env("N8N_SKIP_TLS_VERIFY", default=False):
        raise RuntimeError("N8N_SKIP_TLS_VERIFY must be false in production")
    if _api_auth_required() and not _expected_api_token():
        raise RuntimeError(
            "N8N_EDITOR_AUTH_TOKEN must be configured when API auth is required",
        )


def _authorization_valid(req: Request) -> bool:
    hdr = req.headers.get("authorization", "")
    if not hdr.lower().startswith("bearer "):
        return False
    token = hdr[7:].strip()
    expected = _expected_api_token()
    return bool(expected) and token == expected


def _upstream_error(route: str, e: N8nClientError) -> HTTPException:
    logger.warning(
        "upstream n8n call failed route=%s status=%s err=%s",
        route,
        e.status_code or 502,
        str(e),
    )
    return HTTPException(
        status_code=e.status_code or 502,
        detail={"message": str(e), "status": e.status_code or 502},
    )


@app.middleware("http")
async def enforce_api_auth(request: Request, call_next):
    if request.url.path.startswith("/api") and _api_auth_required():
        if not _authorization_valid(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = request.headers.get("x-request-id", "").strip() or str(uuid4())
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_id=%s method=%s path=%s status=%s duration_ms=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


async def _get_client():
    try:
        r = await multi_config.resolve_active_n8n()
        return client_from_resolved(
            r.base_url,
            r.api_key,
            http_timeout_seconds=r.http_timeout_seconds,
            skip_tls_verify=r.skip_tls_verify,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except N8nClientError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "n8n-workflow-editor",
        "database": True,
        "multi_instance": True,
    }


@app.get("/api/capabilities")
def capabilities():
    return {"database": True, "multi_instance": True}


class N8nSettingsPublic(BaseModel):
    base_url: str | None = None
    api_key_masked: str | None = None
    has_api_key: bool = False
    source: str = "env"
    instance_id: str | None = None
    instance_name: str | None = None


@app.get("/api/settings/n8n", response_model=N8nSettingsPublic)
async def get_n8n_settings():
    try:
        r = await multi_config.resolve_active_n8n()
    except ValueError:
        return N8nSettingsPublic(source="database")
    return N8nSettingsPublic(
        base_url=r.base_url,
        api_key_masked=settings_store.mask_api_key(r.api_key),
        has_api_key=bool(r.api_key),
        source="database",
        instance_id=str(r.instance_id) if r.instance_id else None,
        instance_name=r.instance_name,
    )


class PutN8nSettingsBody(BaseModel):
    base_url: str = Field(..., description="n8n instance root URL")
    api_key: str | None = Field(None, description="If omitted or empty, keep existing saved key")


@app.put("/api/settings/n8n")
async def put_n8n_settings(body: PutN8nSettingsBody):
    try:
        base = settings_store.validate_base_url(body.base_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    new_key = (body.api_key or "").strip()
    try:
        cur = await multi_config.resolve_active_n8n()
        iid = cur.instance_id
    except ValueError:
        iid = None
    if iid:
        await multi_config.update_n8n_instance(
            iid,
            base_url=base,
            api_key=new_key or None,
        )
        return {"ok": True, "source": "database", "instance_id": str(iid)}
    if not new_key:
        raise HTTPException(
            status_code=400,
            detail="api_key is required when no active n8n instance exists yet",
        )
    host = urlparse(base).netloc or "n8n"
    nid = await multi_config.create_n8n_instance(
        name=host,
        base_url=base,
        api_key=new_key,
    )
    prefs = await multi_config.get_preferences()
    llm_raw = prefs.get("active_llm_profile_id")
    await multi_config.set_preferences(
        nid,
        UUID(llm_raw) if llm_raw else None,
    )
    return {"ok": True, "source": "database", "instance_id": str(nid)}


@app.delete("/api/settings/n8n")
async def delete_n8n_settings():
    prefs = await multi_config.get_preferences()
    llm_raw = prefs.get("active_llm_profile_id")
    await multi_config.set_preferences(
        None,
        UUID(llm_raw) if llm_raw else None,
    )
    return {"ok": True, "cleared_active_n8n": True}


@app.post("/api/n8n/test")
async def test_n8n():
    try:
        c = await _get_client()
        data = await c.health_ping()
        return {"ok": True, "sample": data}
    except HTTPException:
        raise
    except N8nClientError as e:
        raise _upstream_error("/api/n8n/test", e) from e


@app.get("/api/workflows")
async def api_list_workflows(
    active: bool | None = None,
    limit: int | None = None,
    cursor: str | None = None,
):
    try:
        c = await _get_client()
        return await c.list_workflows(active=active, limit=limit, cursor=cursor)
    except HTTPException:
        raise
    except N8nClientError as e:
        raise _upstream_error("/api/workflows", e) from e


@app.get("/api/workflows/{workflow_id}")
async def api_get_workflow(workflow_id: str):
    try:
        c = await _get_client()
        return await c.get_workflow(workflow_id)
    except HTTPException:
        raise
    except N8nClientError as e:
        raise _upstream_error("/api/workflows/{workflow_id}", e) from e


@app.post("/api/workflows")
async def api_create_workflow(body: dict[str, Any]):
    try:
        c = await _get_client()
        return await c.create_workflow(body)
    except HTTPException:
        raise
    except N8nClientError as e:
        raise _upstream_error("/api/workflows", e) from e


@app.patch("/api/workflows/{workflow_id}")
async def api_patch_workflow(workflow_id: str, body: dict[str, Any]):
    try:
        c = await _get_client()
        return await c.update_workflow(workflow_id, body)
    except HTTPException:
        raise
    except N8nClientError as e:
        raise _upstream_error("/api/workflows/{workflow_id}", e) from e


@app.delete("/api/workflows/{workflow_id}")
async def api_delete_workflow(workflow_id: str):
    try:
        c = await _get_client()
        return await c.delete_workflow(workflow_id)
    except HTTPException:
        raise
    except N8nClientError as e:
        raise _upstream_error("/api/workflows/{workflow_id}", e) from e


# --- Local workflow cache, sync, and backups ---


@app.get("/api/workflows/local")
async def api_list_local_workflows():
    _require_db()
    try:
        return await workflow_sync.list_local_workflows()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/workflows/local/{workflow_id}")
async def api_get_local_workflow(workflow_id: str):
    _require_db()
    try:
        return await workflow_sync.get_local_workflow(workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.put("/api/workflows/local/{workflow_id}")
async def api_put_local_workflow(workflow_id: str, body: dict[str, Any]):
    _require_db()
    try:
        return await workflow_sync.save_local_workflow(workflow_id, body)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


class SyncQuery(BaseModel):
    force: bool = False


@app.post("/api/workflows/sync")
async def api_sync_all_workflows(force: bool = False):
    _require_db()
    try:
        stats = await workflow_sync.sync_all_from_remote(force=force)
        return {"ok": True, **stats}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except N8nClientError as e:
        raise _upstream_error("/api/workflows/sync", e) from e


@app.post("/api/workflows/local/{workflow_id}/pull")
async def api_pull_workflow(workflow_id: str, force: bool = False):
    _require_db()
    try:
        return await workflow_sync.sync_one_from_remote(workflow_id, force=force)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except N8nClientError as e:
        raise _upstream_error(f"/api/workflows/local/{workflow_id}/pull", e) from e


@app.post("/api/workflows/local/{workflow_id}/push")
async def api_push_workflow(workflow_id: str):
    _require_db()
    try:
        return await workflow_sync.push_to_remote(workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except N8nClientError as e:
        raise _upstream_error(f"/api/workflows/local/{workflow_id}/push", e) from e


class BackupCreateBody(BaseModel):
    label: str | None = None


@app.post("/api/workflows/local/{workflow_id}/backups")
async def api_create_backup(workflow_id: str, body: BackupCreateBody | None = None):
    _require_db()
    try:
        label = body.label if body else None
        return await workflow_sync.create_backup(workflow_id, label=label)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/workflows/local/{workflow_id}/backups")
async def api_list_backups(workflow_id: str):
    _require_db()
    try:
        return await workflow_sync.list_backups(workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/workflows/local/{workflow_id}/backups/{backup_id}")
async def api_get_backup(workflow_id: str, backup_id: UUID):
    _require_db()
    try:
        return await workflow_sync.get_backup(workflow_id, backup_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


class RestoreBackupBody(BaseModel):
    push: bool = False


@app.post("/api/workflows/local/{workflow_id}/backups/{backup_id}/restore")
async def api_restore_backup(workflow_id: str, backup_id: UUID, body: RestoreBackupBody | None = None):
    _require_db()
    push = bool(body.push) if body else False
    try:
        return await workflow_sync.restore_backup(workflow_id, backup_id, push=push)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except N8nClientError as e:
        raise _upstream_error(f"/api/workflows/local/{workflow_id}/backups/{backup_id}/restore", e) from e


@app.get("/api/ai/status")
async def api_ai_status():
    return await ai_status()


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    try:
        return await run_chat(req)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("chat failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


# --- PostgreSQL-backed multi-instance / multi-LLM ---


def _require_db():
    if not multi_config.db_enabled():
        raise HTTPException(status_code=503, detail="PostgreSQL is not available")


class N8nInstanceCreateBody(BaseModel):
    name: str
    base_url: str
    api_key: str
    http_timeout_seconds: float = Field(default=60, ge=1, le=600)
    skip_tls_verify: bool = False


class N8nInstancePatchBody(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    http_timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    skip_tls_verify: bool | None = None


@app.get("/api/n8n-instances")
async def api_list_n8n_instances():
    _require_db()
    return await multi_config.list_n8n_instances()


@app.post("/api/n8n-instances")
async def api_create_n8n_instance(body: N8nInstanceCreateBody):
    _require_db()
    try:
        nid = await multi_config.create_n8n_instance(
            name=body.name,
            base_url=body.base_url,
            api_key=body.api_key,
            http_timeout_seconds=body.http_timeout_seconds,
            skip_tls_verify=body.skip_tls_verify,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": str(nid)}


@app.patch("/api/n8n-instances/{instance_id}")
async def api_patch_n8n_instance(instance_id: UUID, body: N8nInstancePatchBody):
    _require_db()
    try:
        ok = await multi_config.update_n8n_instance(
            instance_id,
            name=body.name,
            base_url=body.base_url,
            api_key=body.api_key,
            http_timeout_seconds=body.http_timeout_seconds,
            skip_tls_verify=body.skip_tls_verify,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"ok": True}


@app.delete("/api/n8n-instances/{instance_id}")
async def api_delete_n8n_instance(instance_id: UUID):
    _require_db()
    ok = await multi_config.delete_n8n_instance(instance_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"ok": True}


class LlmProfileCreateBody(BaseModel):
    name: str
    provider: Literal["azure_openai", "openai_compatible"]
    config: dict[str, Any]


class LlmProfilePatchBody(BaseModel):
    name: str | None = None
    provider: Literal["azure_openai", "openai_compatible"] | None = None
    config: dict[str, Any] | None = None


@app.get("/api/llm-profiles")
async def api_list_llm_profiles():
    _require_db()
    return await multi_config.list_llm_profiles()


@app.post("/api/llm-profiles")
async def api_create_llm_profile(body: LlmProfileCreateBody):
    _require_db()
    try:
        lid = await multi_config.create_llm_profile(name=body.name, provider=body.provider, config=body.config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": str(lid)}


@app.patch("/api/llm-profiles/{profile_id}")
async def api_patch_llm_profile(profile_id: UUID, body: LlmProfilePatchBody):
    _require_db()
    try:
        ok = await multi_config.update_llm_profile(
            profile_id,
            name=body.name,
            provider=body.provider,
            config=body.config,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"ok": True}


@app.delete("/api/llm-profiles/{profile_id}")
async def api_delete_llm_profile(profile_id: UUID):
    _require_db()
    ok = await multi_config.delete_llm_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"ok": True}


class PreferencesBody(BaseModel):
    active_n8n_instance_id: UUID | None = None
    active_llm_profile_id: UUID | None = None


@app.get("/api/preferences")
async def api_get_preferences():
    _require_db()
    return await multi_config.get_preferences()


@app.put("/api/preferences")
async def api_put_preferences(body: PreferencesBody):
    _require_db()
    try:
        await multi_config.set_preferences(body.active_n8n_instance_id, body.active_llm_profile_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


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
