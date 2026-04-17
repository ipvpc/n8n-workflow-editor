"""Multi n8n instances + LLM profiles stored in PostgreSQL."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from . import settings_store
from .database import get_pool

ProviderKind = Literal["azure_openai", "openai_compatible"]


@dataclass(frozen=True)
class ResolvedN8n:
    base_url: str
    api_key: str
    http_timeout_seconds: float
    skip_tls_verify: bool
    instance_id: UUID | None = None
    instance_name: str | None = None


@dataclass(frozen=True)
class ResolvedLlm:
    provider: ProviderKind
    azure_endpoint: str | None
    api_key: str
    api_version: str | None
    azure_deployment: str | None
    base_url: str | None
    model: str | None
    temperature: float
    max_tokens: int


def db_enabled() -> bool:
    return get_pool() is not None


def _env_timeout() -> float:
    try:
        return float(os.environ.get("N8N_HTTP_TIMEOUT_SECONDS", "60"))
    except ValueError:
        return 60.0


def _env_skip_tls() -> bool:
    return os.environ.get("N8N_SKIP_TLS_VERIFY", "").lower() in ("1", "true", "yes")


async def resolve_active_n8n() -> ResolvedN8n:
    pool = get_pool()
    if pool:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT i.id, i.name, i.base_url, i.api_key, i.http_timeout_seconds, i.skip_tls_verify
                FROM app_prefs p
                LEFT JOIN n8n_instance i ON i.id = p.active_n8n_instance_id
                WHERE p.id = 1;
                """
            )
            if row and row["base_url"] and row["api_key"]:
                return ResolvedN8n(
                    str(row["base_url"]).rstrip("/"),
                    str(row["api_key"]),
                    float(row["http_timeout_seconds"] or 60),
                    bool(row["skip_tls_verify"]),
                    instance_id=row["id"],
                    instance_name=str(row["name"]) if row["name"] is not None else None,
                )
        base = os.environ.get("N8N_BASE_URL", "").strip().rstrip("/")
        key = os.environ.get("N8N_API_KEY", "").strip()
        if base and key:
            return ResolvedN8n(base, key, _env_timeout(), _env_skip_tls(), None, None)
        raise ValueError("No active n8n instance and N8N_BASE_URL / N8N_API_KEY are not set")

    base, key = settings_store.resolved_connection()
    if not base or not key:
        raise ValueError("n8n base URL and API key must be configured")
    return ResolvedN8n(base, key, _env_timeout(), _env_skip_tls(), None, None)


async def resolve_active_llm() -> ResolvedLlm | None:
    pool = get_pool()
    if pool:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT l.provider, l.config
                FROM app_prefs p
                JOIN llm_profile l ON l.id = p.active_llm_profile_id
                WHERE p.id = 1;
                """
            )
            if row:
                return _row_to_resolved_llm(str(row["provider"]), row["config"])
        return None

    from .llm_env import llm_config_from_env

    provider, cfg = llm_config_from_env()
    if provider == "none":
        return None
    if provider == "azure_openai":
        c = cfg
        return ResolvedLlm(
            "azure_openai",
            str(c.get("azure_endpoint", "")),
            str(c.get("api_key", "")),
            str(c.get("api_version", "2024-08-01-preview")),
            str(c.get("azure_deployment", "")),
            None,
            None,
            float(os.environ.get("N8N_EDITOR_AI_TEMPERATURE", "0.2")),
            int(os.environ.get("N8N_EDITOR_AI_MAX_TOKENS", "4096")),
        )
    c = cfg
    return ResolvedLlm(
        "openai_compatible",
        None,
        str(c.get("api_key", "")),
        None,
        None,
        str(c.get("base_url", "https://api.openai.com/v1/")).rstrip("/") + "/",
        os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        float(os.environ.get("N8N_EDITOR_AI_TEMPERATURE", "0.2")),
        int(os.environ.get("N8N_EDITOR_AI_MAX_TOKENS", "4096")),
    )


def _row_to_resolved_llm(provider: str, config: Any) -> ResolvedLlm:
    if isinstance(config, str):
        cfg = json.loads(config)
    else:
        cfg = dict(config) if config else {}
    temp = float(cfg.get("temperature", 0.2))
    max_tok = int(cfg.get("max_tokens", 4096))
    if provider == "azure_openai":
        return ResolvedLlm(
            "azure_openai",
            str(cfg.get("azure_endpoint", "")).rstrip("/"),
            str(cfg.get("api_key", "")),
            str(cfg.get("api_version", "2024-08-01-preview")),
            str(cfg.get("azure_deployment", "")),
            None,
            None,
            temp,
            max_tok,
        )
    base = str(cfg.get("base_url", "https://api.openai.com/v1")).strip().rstrip("/")
    return ResolvedLlm(
        "openai_compatible",
        None,
        str(cfg.get("api_key", "")),
        None,
        None,
        base + "/",
        str(cfg.get("model", "gpt-4o-mini")).strip() or "gpt-4o-mini",
        temp,
        max_tok,
    )


def _mask_config_public(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    if "api_key" in out and out["api_key"]:
        out["api_key_masked"] = settings_store.mask_api_key(str(out["api_key"]))
        del out["api_key"]
    return out


# --- CRUD (require pool) ---


async def list_n8n_instances() -> list[dict[str, Any]]:
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, base_url, api_key, http_timeout_seconds, skip_tls_verify, created_at
            FROM n8n_instance
            ORDER BY name;
            """
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": str(r["id"]),
                "name": r["name"],
                "base_url": r["base_url"],
                "api_key_masked": settings_store.mask_api_key(str(r["api_key"])),
                "http_timeout_seconds": float(r["http_timeout_seconds"] or 60),
                "skip_tls_verify": bool(r["skip_tls_verify"]),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
        )
    return out


async def create_n8n_instance(
    *,
    name: str,
    base_url: str,
    api_key: str,
    http_timeout_seconds: float = 60,
    skip_tls_verify: bool = False,
) -> UUID:
    pool = get_pool()
    assert pool
    base = settings_store.validate_base_url(base_url)
    async with pool.acquire() as conn:
        nid = await conn.fetchval(
            """
            INSERT INTO n8n_instance (name, base_url, api_key, http_timeout_seconds, skip_tls_verify)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id;
            """,
            name.strip() or "Unnamed",
            base,
            api_key.strip(),
            http_timeout_seconds,
            skip_tls_verify,
        )
    return nid


async def update_n8n_instance(
    instance_id: UUID,
    *,
    name: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    http_timeout_seconds: float | None = None,
    skip_tls_verify: bool | None = None,
) -> bool:
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM n8n_instance WHERE id = $1", instance_id)
        if not row:
            return False
        n = name if name is not None else row["name"]
        bu = settings_store.validate_base_url(base_url) if base_url is not None else row["base_url"]
        ak = api_key.strip() if api_key is not None and api_key.strip() else row["api_key"]
        to = http_timeout_seconds if http_timeout_seconds is not None else float(row["http_timeout_seconds"] or 60)
        sk = skip_tls_verify if skip_tls_verify is not None else bool(row["skip_tls_verify"])
        await conn.execute(
            """
            UPDATE n8n_instance
            SET name = $2, base_url = $3, api_key = $4, http_timeout_seconds = $5, skip_tls_verify = $6, updated_at = now()
            WHERE id = $1;
            """,
            instance_id,
            n,
            bu,
            ak,
            to,
            sk,
        )
    return True


async def delete_n8n_instance(instance_id: UUID) -> bool:
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM n8n_instance WHERE id = $1 RETURNING id", instance_id)
    return row is not None


async def list_llm_profiles() -> list[dict[str, Any]]:
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, provider, config, created_at FROM llm_profile ORDER BY name;"
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        cfg = dict(r["config"]) if r["config"] else {}
        out.append(
            {
                "id": str(r["id"]),
                "name": r["name"],
                "provider": r["provider"],
                "config_public": _mask_config_public(dict(cfg)),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
        )
    return out


def _validate_llm_config(provider: ProviderKind, cfg: dict[str, Any]) -> dict[str, Any]:
    if provider == "azure_openai":
        ep = str(cfg.get("azure_endpoint", "")).strip().rstrip("/")
        key = str(cfg.get("api_key", "")).strip()
        dep = str(cfg.get("azure_deployment", "")).strip()
        if not ep or not key or not dep:
            raise ValueError("azure_openai requires azure_endpoint, api_key, and azure_deployment in config")
        return {
            "azure_endpoint": ep,
            "api_key": key,
            "azure_deployment": dep,
            "api_version": str(cfg.get("api_version", "2024-08-01-preview")).strip(),
            "temperature": float(cfg.get("temperature", 0.2)),
            "max_tokens": int(cfg.get("max_tokens", 4096)),
        }
    key = str(cfg.get("api_key", "")).strip()
    if not key:
        raise ValueError("openai_compatible requires api_key in config")
    base = str(cfg.get("base_url", "https://api.openai.com/v1")).strip().rstrip("/")
    return {
        "api_key": key,
        "base_url": base,
        "model": str(cfg.get("model", "gpt-4o-mini")).strip() or "gpt-4o-mini",
        "temperature": float(cfg.get("temperature", 0.2)),
        "max_tokens": int(cfg.get("max_tokens", 4096)),
    }


async def create_llm_profile(*, name: str, provider: ProviderKind, config: dict[str, Any]) -> UUID:
    pool = get_pool()
    assert pool
    cfg = _validate_llm_config(provider, config)
    async with pool.acquire() as conn:
        lid = await conn.fetchval(
            """
            INSERT INTO llm_profile (name, provider, config)
            VALUES ($1, $2, $3::jsonb)
            RETURNING id;
            """,
            name.strip() or "Unnamed",
            provider,
            json.dumps(cfg),
        )
    return lid


async def update_llm_profile(
    profile_id: UUID,
    *,
    name: str | None = None,
    provider: ProviderKind | None = None,
    config: dict[str, Any] | None = None,
) -> bool:
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT name, provider, config FROM llm_profile WHERE id = $1", profile_id)
        if not row:
            return False
        n = name if name is not None else row["name"]
        prov = provider if provider is not None else str(row["provider"])
        old_cfg = dict(row["config"]) if row["config"] else {}
        if provider is not None and provider != str(row["provider"]) and config is None:
            raise ValueError("config is required when changing provider type")
        if config is not None:
            merged = {**old_cfg, **config}
            new_cfg = _validate_llm_config(prov, merged)  # type: ignore[arg-type]
        else:
            new_cfg = dict(old_cfg)
        await conn.execute(
            """
            UPDATE llm_profile SET name = $2, provider = $3, config = $4::jsonb, updated_at = now()
            WHERE id = $1;
            """,
            profile_id,
            n,
            prov,
            json.dumps(new_cfg),
        )
    return True


async def delete_llm_profile(profile_id: UUID) -> bool:
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM llm_profile WHERE id = $1 RETURNING id", profile_id)
    return row is not None


async def get_preferences() -> dict[str, Any]:
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT active_n8n_instance_id, active_llm_profile_id FROM app_prefs WHERE id = 1"
        )
    return {
        "active_n8n_instance_id": str(row["active_n8n_instance_id"]) if row and row["active_n8n_instance_id"] else None,
        "active_llm_profile_id": str(row["active_llm_profile_id"]) if row and row["active_llm_profile_id"] else None,
    }


async def set_preferences(
    active_n8n_instance_id: UUID | None,
    active_llm_profile_id: UUID | None,
) -> None:
    """Set active targets; use None for a slot to clear it."""
    pool = get_pool()
    assert pool
    async with pool.acquire() as conn:
        if active_n8n_instance_id is not None:
            exists = await conn.fetchval("SELECT 1 FROM n8n_instance WHERE id = $1", active_n8n_instance_id)
            if not exists:
                raise ValueError("active_n8n_instance_id not found")
        if active_llm_profile_id is not None:
            exists = await conn.fetchval("SELECT 1 FROM llm_profile WHERE id = $1", active_llm_profile_id)
            if not exists:
                raise ValueError("active_llm_profile_id not found")
        await conn.execute(
            """
            UPDATE app_prefs
            SET active_n8n_instance_id = $2, active_llm_profile_id = $3, updated_at = now()
            WHERE id = $1;
            """,
            1,
            active_n8n_instance_id,
            active_llm_profile_id,
        )


async def ai_status_from_db() -> dict[str, Any]:
    llm = await resolve_active_llm()
    if not llm:
        return {"enabled": False, "provider": "none", "source": "database" if db_enabled() else "env"}
    if llm.provider == "azure_openai":
        ep = llm.azure_endpoint or ""
        masked = re.sub(r"https?://([^.]+)\.", r"https://***.", ep, count=1) if ep else ""
        return {
            "enabled": True,
            "provider": "azure_openai",
            "endpoint": masked,
            "deployment": llm.azure_deployment,
            "source": "database" if db_enabled() else "env",
        }
    return {
        "enabled": True,
        "provider": "openai_compatible",
        "base_url": llm.base_url,
        "model": llm.model,
        "source": "database" if db_enabled() else "env",
    }
