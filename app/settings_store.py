"""Persist n8n connection settings to disk (overrides env when present)."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = "/data"
_FILE_NAME = "n8n-connection.json"


def _data_dir() -> Path:
    return Path(os.environ.get("N8N_WORKFLOW_EDITOR_DATA_DIR", _DEFAULT_DATA_DIR)).resolve()


def _file_path() -> Path:
    return _data_dir() / _FILE_NAME


def _mask_key(key: str) -> str:
    k = key.strip()
    if len(k) <= 8:
        return "••••••••"
    return f"••••••••{k[-4:]}"


def mask_api_key(key: str) -> str:
    """Public helper for masked API key display."""
    return _mask_key(key)


class N8nConnectionSettings(BaseModel):
    """Stored connection; empty strings mean 'not set in file, fall back to env'."""

    base_url: str = Field(default="", description="n8n root URL, e.g. https://n8n.example.com")
    api_key: str = Field(default="", description="X-N8N-API-KEY value")

    @field_validator("base_url")
    @classmethod
    def strip_url(cls, v: str) -> str:
        return v.strip().rstrip("/")

    def model_dump_public(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url or None,
            "api_key_masked": _mask_key(self.api_key) if self.api_key else None,
            "has_api_key": bool(self.api_key),
        }


def _ensure_dir() -> None:
    p = _data_dir()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("could not create data dir %s", p)


def load_settings() -> N8nConnectionSettings | None:
    path = _file_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return N8nConnectionSettings.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("failed to load %s: %s", path, e)
        return None


def save_settings(settings: N8nConnectionSettings) -> None:
    _ensure_dir()
    path = _file_path()
    path.write_text(
        json.dumps(settings.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def delete_settings_file() -> bool:
    path = _file_path()
    if path.is_file():
        try:
            path.unlink()
            return True
        except OSError:
            logger.exception("failed to remove %s", path)
    return False


def _env_base_url() -> str:
    return os.environ.get("N8N_BASE_URL", "").strip().rstrip("/")


def _env_api_key() -> str:
    return os.environ.get("N8N_API_KEY", "").strip()


def resolved_connection() -> tuple[str, str]:
    """
    Volume file overrides env when file exists and provides non-empty values.
    Per-field: file value if non-empty else env.
    """
    f = load_settings()
    base = (f.base_url if f and f.base_url else "") or _env_base_url()
    key = (f.api_key if f and f.api_key else "") or _env_api_key()
    return base, key


def validate_base_url(url: str) -> str:
    u = url.strip().rstrip("/")
    if not u:
        raise ValueError("base_url is required")
    if not re.match(r"^https?://", u, re.I):
        raise ValueError("base_url must start with http:// or https://")
    return u
