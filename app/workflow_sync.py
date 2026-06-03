"""Local workflow cache, sync with n8n, and backups."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .database import get_pool
from .multi_config import resolve_active_n8n
from .n8n_client import N8nClient, N8nClientError, client_from_resolved

logger = logging.getLogger(__name__)


def _require_pool():
    pool = get_pool()
    if pool is None:
        raise RuntimeError("DATABASE_URL is not configured")
    return pool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _workflow_name(data: dict[str, Any]) -> str:
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "Unnamed"


def _workflow_active(data: dict[str, Any]) -> bool | None:
    active = data.get("active")
    return bool(active) if active is not None else None


def _parse_remote_updated_at(data: dict[str, Any]) -> datetime | None:
    for key in ("updatedAt", "updated_at"):
        raw = data.get(key)
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def _patch_body(data: dict[str, Any]) -> dict[str, Any]:
    """Send only fields n8n PATCH typically accepts."""
    allowed = (
        "name",
        "nodes",
        "connections",
        "settings",
        "staticData",
        "meta",
        "pinData",
        "tags",
        "active",
    )
    return {k: data[k] for k in allowed if k in data}


async def _active_instance_id() -> UUID:
    resolved = await resolve_active_n8n()
    if not resolved.instance_id:
        raise ValueError("Active n8n instance must be stored in the database for workflow sync")
    return resolved.instance_id


async def _client() -> N8nClient:
    r = await resolve_active_n8n()
    return client_from_resolved(
        r.base_url,
        r.api_key,
        http_timeout_seconds=r.http_timeout_seconds,
        skip_tls_verify=r.skip_tls_verify,
    )


async def list_local_workflows() -> list[dict[str, Any]]:
    pool = _require_pool()
    iid = await _active_instance_id()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT remote_workflow_id, name, active, is_dirty, synced_at, local_updated_at, remote_updated_at
            FROM workflow_copy
            WHERE n8n_instance_id = $1
            ORDER BY name;
            """,
            iid,
        )
    return [
        {
            "id": str(r["remote_workflow_id"]),
            "name": r["name"],
            "active": r["active"],
            "is_dirty": bool(r["is_dirty"]),
            "synced_at": r["synced_at"].isoformat() if r["synced_at"] else None,
            "local_updated_at": r["local_updated_at"].isoformat() if r["local_updated_at"] else None,
            "remote_updated_at": r["remote_updated_at"].isoformat() if r["remote_updated_at"] else None,
        }
        for r in rows
    ]


def _json_to_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(value)


async def get_local_workflow(remote_workflow_id: str) -> dict[str, Any]:
    pool = _require_pool()
    iid = await _active_instance_id()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT workflow_json, name, is_dirty, synced_at, local_updated_at
            FROM workflow_copy
            WHERE n8n_instance_id = $1 AND remote_workflow_id = $2;
            """,
            iid,
            remote_workflow_id,
        )
    if not row:
        raise ValueError("Workflow not found in local cache. Run Sync from n8n first.")
    data = _json_to_dict(row["workflow_json"])
    data["_local"] = {
        "is_dirty": bool(row["is_dirty"]),
        "synced_at": row["synced_at"].isoformat() if row["synced_at"] else None,
        "local_updated_at": row["local_updated_at"].isoformat() if row["local_updated_at"] else None,
    }
    return data


async def save_local_workflow(remote_workflow_id: str, body: dict[str, Any]) -> dict[str, Any]:
    pool = _require_pool()
    iid = await _active_instance_id()
    clean = {k: v for k, v in body.items() if k != "_local"}
    now = _utcnow()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE workflow_copy
            SET workflow_json = $3::jsonb,
                name = $4,
                active = $5,
                local_updated_at = $6,
                is_dirty = TRUE
            WHERE n8n_instance_id = $1 AND remote_workflow_id = $2
            RETURNING id;
            """,
            iid,
            remote_workflow_id,
            json.dumps(clean),
            _workflow_name(clean),
            _workflow_active(clean),
            now,
        )
    if not row:
        raise ValueError("Workflow not found in local cache. Run Sync from n8n first.")
    return await get_local_workflow(remote_workflow_id)


async def _upsert_local(
    conn,
    *,
    instance_id: UUID,
    remote_workflow_id: str,
    data: dict[str, Any],
) -> str:
    now = _utcnow()
    remote_updated = _parse_remote_updated_at(data)
    existed = await conn.fetchval(
        """
        SELECT 1 FROM workflow_copy
        WHERE n8n_instance_id = $1 AND remote_workflow_id = $2;
        """,
        instance_id,
        remote_workflow_id,
    )
    await conn.execute(
        """
        INSERT INTO workflow_copy (
            n8n_instance_id, remote_workflow_id, name, active, workflow_json,
            remote_updated_at, synced_at, local_updated_at, is_dirty
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $7, FALSE)
        ON CONFLICT (n8n_instance_id, remote_workflow_id) DO UPDATE SET
            name = EXCLUDED.name,
            active = EXCLUDED.active,
            workflow_json = EXCLUDED.workflow_json,
            remote_updated_at = EXCLUDED.remote_updated_at,
            synced_at = EXCLUDED.synced_at,
            local_updated_at = EXCLUDED.synced_at,
            is_dirty = FALSE;
        """,
        instance_id,
        remote_workflow_id,
        _workflow_name(data),
        _workflow_active(data),
        json.dumps(data),
        remote_updated,
        now,
    )
    return "created" if not existed else "updated"


async def sync_all_from_remote(*, force: bool = False) -> dict[str, int]:
    client = await _client()
    iid = await _active_instance_id()
    pool = _require_pool()

    created = updated = skipped = 0
    cursor: str | None = None

    while True:
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = await client.list_workflows(limit=100, cursor=cursor)
        items: list[dict[str, Any]] = []
        next_cursor: str | None = None
        if isinstance(page, list):
            items = page
        elif isinstance(page, dict):
            raw = page.get("data") or page.get("workflows")
            if isinstance(raw, list):
                items = raw
            next_cursor = page.get("nextCursor") or page.get("cursor")

        for item in items:
            wid = str(item.get("id", "")).strip()
            if not wid:
                continue
            async with pool.acquire() as conn:
                if not force:
                    existing = await conn.fetchrow(
                        """
                        SELECT is_dirty FROM workflow_copy
                        WHERE n8n_instance_id = $1 AND remote_workflow_id = $2;
                        """,
                        iid,
                        wid,
                    )
                    if existing and existing["is_dirty"]:
                        skipped += 1
                        continue
            full = await client.get_workflow(wid)
            if not isinstance(full, dict):
                continue
            async with pool.acquire() as conn:
                result = await _upsert_local(
                    conn,
                    instance_id=iid,
                    remote_workflow_id=wid,
                    data=full,
                )
            if result == "created":
                created += 1
            else:
                updated += 1

        if not next_cursor or not items:
            break
        cursor = next_cursor

    return {"created": created, "updated": updated, "skipped": skipped}


async def sync_one_from_remote(remote_workflow_id: str, *, force: bool = False) -> dict[str, Any]:
    pool = _require_pool()
    iid = await _active_instance_id()
    if not force:
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                """
                SELECT is_dirty FROM workflow_copy
                WHERE n8n_instance_id = $1 AND remote_workflow_id = $2;
                """,
                iid,
                remote_workflow_id,
            )
            if existing and existing["is_dirty"]:
                raise ValueError("Local workflow has unsynced changes. Use force=true or push first.")

    client = await _client()
    full = await client.get_workflow(remote_workflow_id)
    if not isinstance(full, dict):
        raise ValueError("Invalid workflow response from n8n")
    async with pool.acquire() as conn:
        await _upsert_local(
            conn,
            instance_id=iid,
            remote_workflow_id=remote_workflow_id,
            data=full,
        )
    return await get_local_workflow(remote_workflow_id)


async def push_to_remote(remote_workflow_id: str, *, backup_before: bool = True) -> dict[str, Any]:
    local = await get_local_workflow(remote_workflow_id)
    local_meta = local.pop("_local", {})
    if backup_before:
        await create_backup(remote_workflow_id, label="pre-push", source="pre_push")

    client = await _client()
    patch = _patch_body(local)
    try:
        result = await client.update_workflow(remote_workflow_id, patch)
    except N8nClientError:
        raise

    if isinstance(result, dict):
        data = result
    else:
        data = local

    pool = _require_pool()
    iid = await _active_instance_id()
    now = _utcnow()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE workflow_copy
            SET workflow_json = $3::jsonb,
                name = $4,
                active = $5,
                remote_updated_at = $6,
                synced_at = $6,
                local_updated_at = $6,
                is_dirty = FALSE
            WHERE n8n_instance_id = $1 AND remote_workflow_id = $2;
            """,
            iid,
            remote_workflow_id,
            json.dumps(data),
            _workflow_name(data),
            _workflow_active(data),
            _parse_remote_updated_at(data) or now,
        )
    out = dict(data)
    out["_local"] = {**local_meta, "is_dirty": False, "synced_at": now.isoformat()}
    return out


async def _copy_id(instance_id: UUID, remote_workflow_id: str) -> UUID:
    pool = _require_pool()
    async with pool.acquire() as conn:
        cid = await conn.fetchval(
            """
            SELECT id FROM workflow_copy
            WHERE n8n_instance_id = $1 AND remote_workflow_id = $2;
            """,
            instance_id,
            remote_workflow_id,
        )
    if not cid:
        raise ValueError("Workflow not found in local cache")
    return cid


async def create_backup(
    remote_workflow_id: str,
    *,
    label: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    pool = _require_pool()
    iid = await _active_instance_id()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, name, workflow_json FROM workflow_copy
            WHERE n8n_instance_id = $1 AND remote_workflow_id = $2;
            """,
            iid,
            remote_workflow_id,
        )
        if not row:
            raise ValueError("Workflow not found in local cache")
        bid = await conn.fetchval(
            """
            INSERT INTO workflow_backup (workflow_copy_id, name, label, workflow_json, source)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            RETURNING id;
            """,
            row["id"],
            row["name"],
            label or "Manual backup",
            json.dumps(_json_to_dict(row["workflow_json"])),
            source,
        )
    return {"id": str(bid), "label": label or "Manual backup", "source": source}


async def list_backups(remote_workflow_id: str) -> list[dict[str, Any]]:
    pool = _require_pool()
    iid = await _active_instance_id()
    cid = await _copy_id(iid, remote_workflow_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, label, source, created_at
            FROM workflow_backup
            WHERE workflow_copy_id = $1
            ORDER BY created_at DESC;
            """,
            cid,
        )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "label": r["label"],
            "source": r["source"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def get_backup(remote_workflow_id: str, backup_id: UUID) -> dict[str, Any]:
    pool = _require_pool()
    iid = await _active_instance_id()
    cid = await _copy_id(iid, remote_workflow_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT workflow_json, label, created_at
            FROM workflow_backup
            WHERE id = $1 AND workflow_copy_id = $2;
            """,
            backup_id,
            cid,
        )
    if not row:
        raise ValueError("Backup not found")
    data = _json_to_dict(row["workflow_json"])
    data["_backup"] = {
        "label": row["label"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    return data


async def restore_backup(
    remote_workflow_id: str,
    backup_id: UUID,
    *,
    push: bool = False,
) -> dict[str, Any]:
    data = await get_backup(remote_workflow_id, backup_id)
    data.pop("_backup", None)
    await save_local_workflow(remote_workflow_id, data)
    if push:
        return await push_to_remote(remote_workflow_id, backup_before=True)
    return await get_local_workflow(remote_workflow_id)
