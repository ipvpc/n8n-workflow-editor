from unittest.mock import AsyncMock

import pytest

from app.n8n_client import N8nClient, N8nClientError


@pytest.mark.asyncio
async def test_verify_access_read_and_write_ok() -> None:
    client = N8nClient("http://n8n.test", "secret")
    client._request = AsyncMock(return_value={"data": []})  # type: ignore[method-assign]
    client.create_workflow = AsyncMock(return_value={"id": "wf-probe"})  # type: ignore[method-assign]
    client.delete_workflow = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await client.verify_access()

    assert result["ok"] is True
    assert result["read"] is True
    assert result["write"] is True
    client.create_workflow.assert_awaited_once()
    client.delete_workflow.assert_awaited_once_with("wf-probe")


@pytest.mark.asyncio
async def test_verify_access_read_only_fails() -> None:
    client = N8nClient("http://n8n.test", "secret")
    client._request = AsyncMock(return_value={"data": []})  # type: ignore[method-assign]
    client.create_workflow = AsyncMock(  # type: ignore[method-assign]
        side_effect=N8nClientError("n8n returned 403", status_code=403)
    )
    client.delete_workflow = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await client.verify_access()

    assert result["ok"] is False
    assert result["read"] is True
    assert result["write"] is False
    assert "403" in (result["write_error"] or "")


@pytest.mark.asyncio
async def test_verify_access_read_fails() -> None:
    client = N8nClient("http://n8n.test", "secret")
    client._request = AsyncMock(  # type: ignore[method-assign]
        side_effect=N8nClientError("n8n returned 401", status_code=401)
    )
    client.create_workflow = AsyncMock()  # type: ignore[method-assign]

    result = await client.verify_access()

    assert result["ok"] is False
    assert result["read"] is False
    assert result["write"] is False
    client.create_workflow.assert_not_awaited()
