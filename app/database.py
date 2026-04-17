"""Async PostgreSQL pool (optional; enabled when DATABASE_URL is set)."""

from __future__ import annotations

import logging
import os
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def database_url() -> str | None:
    u = os.environ.get("DATABASE_URL", "").strip()
    return u or None


async def init_db() -> None:
    global _pool
    url = database_url()
    if not url:
        _pool = None
        return
    _pool = await asyncpg.create_pool(url, min_size=1, max_size=10)
    async with _pool.acquire() as conn:
        await _ensure_schema(conn)
        await _bootstrap_from_env(conn)
    logger.info("PostgreSQL pool ready")


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    return _pool


async def _ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS n8n_instance (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            http_timeout_seconds DOUBLE PRECISION NOT NULL DEFAULT 60,
            skip_tls_verify BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_profile (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            provider TEXT NOT NULL,
            config JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT llm_profile_provider_chk CHECK (provider IN ('azure_openai', 'openai_compatible'))
        );
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_prefs (
            id SMALLINT PRIMARY KEY,
            active_n8n_instance_id UUID REFERENCES n8n_instance(id) ON DELETE SET NULL,
            active_llm_profile_id UUID REFERENCES llm_profile(id) ON DELETE SET NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT app_prefs_singleton_chk CHECK (id = 1)
        );
        """
    )
    await conn.execute(
        """
        INSERT INTO app_prefs (id)
        SELECT 1
        WHERE NOT EXISTS (SELECT 1 FROM app_prefs WHERE id = 1);
        """
    )


async def _bootstrap_from_env(conn: asyncpg.Connection) -> None:
    import json
    import os

    n = await conn.fetchval("SELECT COUNT(*)::int FROM n8n_instance")
    if n == 0:
        base = os.environ.get("N8N_BASE_URL", "").strip().rstrip("/")
        key = os.environ.get("N8N_API_KEY", "").strip()
        if base and key:
            try:
                to = float(os.environ.get("N8N_HTTP_TIMEOUT_SECONDS", "60"))
            except ValueError:
                to = 60.0
            skip = os.environ.get("N8N_SKIP_TLS_VERIFY", "").lower() in ("1", "true", "yes")
            nid = await conn.fetchval(
                """
                INSERT INTO n8n_instance (name, base_url, api_key, http_timeout_seconds, skip_tls_verify)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id;
                """,
                "Default (from env)",
                base,
                key,
                to,
                skip,
            )
            await conn.execute(
                "UPDATE app_prefs SET active_n8n_instance_id = $1, updated_at = now() WHERE id = 1",
                nid,
            )
            logger.info("Bootstrapped n8n_instance from N8N_BASE_URL / N8N_API_KEY")

    m = await conn.fetchval("SELECT COUNT(*)::int FROM llm_profile")
    if m == 0:
        azure_ep = os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("AZURE_OPENAI_ENDPOINT", "")).strip()
        azure_key = os.environ.get("AZURE_AI_API_KEY", os.environ.get("AZURE_OPENAI_API_KEY", "")).strip()
        azure_dep = os.environ.get("AZURE_AI_DEPLOYMENT", os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")).strip()
        api_ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        openai_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        temp = os.environ.get("N8N_EDITOR_AI_TEMPERATURE", "0.2")
        max_tok = os.environ.get("N8N_EDITOR_AI_MAX_TOKENS", "4096")

        cfg: dict[str, Any]
        provider: str | None = None
        if azure_ep and azure_key and azure_dep:
            provider = "azure_openai"
            cfg = {
                "azure_endpoint": azure_ep.rstrip("/"),
                "api_key": azure_key,
                "azure_deployment": azure_dep,
                "api_version": api_ver,
                "temperature": float(temp) if temp else 0.2,
                "max_tokens": int(max_tok) if max_tok.isdigit() else 4096,
            }
        elif openai_key:
            provider = "openai_compatible"
            cfg = {
                "api_key": openai_key,
                "base_url": openai_base,
                "model": openai_model,
                "temperature": float(temp) if temp else 0.2,
                "max_tokens": int(max_tok) if max_tok.isdigit() else 4096,
            }

        if provider:
            lid = await conn.fetchval(
                """
                INSERT INTO llm_profile (name, provider, config)
                VALUES ($1, $2, $3::jsonb)
                RETURNING id;
                """,
                "Default (from env)",
                provider,
                json.dumps(cfg),
            )
            await conn.execute(
                "UPDATE app_prefs SET active_llm_profile_id = $1, updated_at = now() WHERE id = 1",
                lid,
            )
            logger.info("Bootstrapped llm_profile from environment")
