"""OpenAI / Azure LLM configuration from environment variables only."""

from __future__ import annotations

import os
import re
from typing import Any


def _sanitize_endpoint(url: str) -> str:
    u = url.strip().rstrip("/")
    while True:
        if u.startswith("https://https://"):
            u = "https://" + u[16:]
        elif u.startswith("http://https://"):
            u = "https://" + u[13:]
        else:
            break
    return u


def llm_config_from_env() -> tuple[str, dict[str, Any]]:
    """Returns (provider, kwargs for AsyncOpenAI / AsyncAzureOpenAI)."""
    azure_ep = _sanitize_endpoint(
        os.environ.get("AZURE_AI_ENDPOINT", os.environ.get("AZURE_OPENAI_ENDPOINT", "")).strip()
    )
    azure_key = os.environ.get("AZURE_AI_API_KEY", os.environ.get("AZURE_OPENAI_API_KEY", "")).strip()
    azure_dep = os.environ.get("AZURE_AI_DEPLOYMENT", os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")).strip()
    api_ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview").strip()

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")

    if azure_ep and azure_key and azure_dep:
        return (
            "azure_openai",
            {
                "azure_endpoint": azure_ep,
                "api_key": azure_key,
                "api_version": api_ver,
                "azure_deployment": azure_dep,
            },
        )
    if openai_key:
        return (
            "openai_compatible",
            {"api_key": openai_key, "base_url": openai_base + "/"},
        )
    return ("none", {})


def ai_status_from_env() -> dict[str, Any]:
    provider, cfg = llm_config_from_env()
    enabled = provider != "none"
    out: dict[str, Any] = {"enabled": enabled, "provider": provider, "source": "env"}
    if provider == "azure_openai":
        ep = str(cfg.get("azure_endpoint", ""))
        out["endpoint"] = re.sub(r"https?://([^.]+)\.", r"https://***.", ep, count=1) if ep else ""
        out["deployment"] = cfg.get("azure_deployment", "")
    elif provider == "openai_compatible":
        out["base_url"] = cfg.get("base_url", "")
        out["model"] = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    return out
