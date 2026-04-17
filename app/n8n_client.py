"""Async HTTP client for n8n Public REST API."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _skip_tls_verify() -> bool:
    return os.environ.get("N8N_SKIP_TLS_VERIFY", "").lower() in ("1", "true", "yes")


def _timeout_seconds() -> float:
    try:
        return float(os.environ.get("N8N_HTTP_TIMEOUT_SECONDS", "60"))
    except ValueError:
        return 60.0


class N8nClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class N8nClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self._verify = not _skip_tls_verify()
        self._timeout = httpx.Timeout(_timeout_seconds())

    def _headers(self, content_json: bool) -> dict[str, str]:
        h = {
            "X-N8N-API-KEY": self.api_key,
            "Accept": "application/json",
        }
        if content_json:
            h["Content-Type"] = "application/json"
        return h

    def _api_root(self) -> str:
        return f"{self.base_url}/api/v1"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        url = f"{self._api_root()}{path}"
        content_json = json_body is not None
        async with httpx.AsyncClient(verify=self._verify, timeout=self._timeout) as client:
            try:
                r = await client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._headers(content_json),
                )
            except httpx.RequestError as e:
                raise N8nClientError(f"n8n request failed: {e}") from e

        text = r.text
        if r.status_code >= 400:
            logger.warning("n8n error %s %s: %s", method, path, r.status_code)
            raise N8nClientError(
                f"n8n returned {r.status_code}",
                status_code=r.status_code,
                body=text[:8000],
            )

        if not text.strip():
            return None
        try:
            return r.json()
        except Exception:
            return text

    async def health_ping(self) -> dict[str, Any]:
        """Minimal call to verify auth (list with limit 1)."""
        return await self._request("GET", "/workflows", params={"limit": 1})

    async def list_workflows(
        self,
        *,
        active: bool | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if active is not None:
            params["active"] = str(active).lower()
        if limit is not None:
            params["limit"] = limit
        if cursor:
            params["cursor"] = cursor
        return await self._request("GET", "/workflows", params=params or None)

    async def get_workflow(self, workflow_id: str) -> Any:
        return await self._request("GET", f"/workflows/{workflow_id}")

    async def create_workflow(self, body: dict[str, Any]) -> Any:
        return await self._request("POST", "/workflows", json_body=body)

    async def update_workflow(self, workflow_id: str, body: dict[str, Any]) -> Any:
        return await self._request("PATCH", f"/workflows/{workflow_id}", json_body=body)

    async def delete_workflow(self, workflow_id: str) -> Any:
        return await self._request("DELETE", f"/workflows/{workflow_id}")


def client_from_resolved(base_url: str, api_key: str) -> N8nClient:
    if not base_url or not api_key:
        raise N8nClientError("n8n base URL and API key must be configured")
    return N8nClient(base_url, api_key)
