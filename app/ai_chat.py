"""LLM chat with optional tools for n8n workflow get/update."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import multi_config
from .llm_env import ai_status_from_env
from .n8n_knowledge import N8N_KNOWLEDGE_PACK
from .n8n_client import N8nClientError, client_from_resolved

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8


class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str | None = ""
    tool_call_id: str | None = None
    name: str | None = None


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(default_factory=list)
    workflow_id: str | None = None
    """Current workflow id in the editor (for tools and context)."""
    workflow_json: str | None = None
    """Optional stringified workflow JSON from the editor for context."""


async def ai_status() -> dict[str, Any]:
    if multi_config.db_enabled():
        return await multi_config.ai_status_from_db()
    return ai_status_from_env()


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "n8n_get_workflow",
            "description": "Fetch a workflow by ID from the configured n8n instance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "description": "n8n workflow id"},
                },
                "required": ["workflow_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "n8n_update_workflow",
            "description": "Apply a PATCH-style update to a workflow. Use dry_run=true to validate without saving.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "patch": {
                        "type": "object",
                        "description": "JSON object merged as PATCH body (n8n API).",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "If true, do not call n8n; only validate JSON.",
                    },
                },
                "required": ["workflow_id", "patch", "dry_run"],
            },
        },
    },
]


def _system_prompt() -> str:
    return f"""You are an expert n8n workflow assistant embedded in a workflow editor.
Help the user design, debug, and safely change n8n workflows. Be concise and practical.

{N8N_KNOWLEDGE_PACK}

Rules:
- Prefer suggesting incremental edits. When proposing workflow JSON, wrap complete JSON in a fenced code block with language json.
- Use tools to read the live workflow or apply updates when needed. Use dry_run=true first for risky changes unless the user asked to apply immediately.
- Never invent API keys or credentials; never echo secrets from tools.
- If the n8n API returns an error, explain likely causes (auth, scopes, invalid body).
"""


async def _run_tool(name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid tool arguments: {e}"})

    try:
        r = await multi_config.resolve_active_n8n()
        client = client_from_resolved(
            r.base_url,
            r.api_key,
            http_timeout_seconds=r.http_timeout_seconds,
            skip_tls_verify=r.skip_tls_verify,
        )
    except (ValueError, N8nClientError) as e:
        return json.dumps({"error": str(e)})

    if name == "n8n_get_workflow":
        wid = str(args.get("workflow_id", "")).strip()
        if not wid:
            return json.dumps({"error": "workflow_id required"})
        try:
            data = await client.get_workflow(wid)
            return json.dumps(data, default=str)[:200_000]
        except N8nClientError as e:
            return json.dumps({"error": str(e), "status": e.status_code, "body": e.body})

    if name == "n8n_update_workflow":
        wid = str(args.get("workflow_id", "")).strip()
        patch = args.get("patch")
        dry = bool(args.get("dry_run", True))
        if not wid or not isinstance(patch, dict):
            return json.dumps({"error": "workflow_id and patch object required"})
        if dry:
            return json.dumps({"dry_run": True, "would_send": patch})
        try:
            data = await client.update_workflow(wid, patch)
            return json.dumps(data, default=str)[:200_000]
        except N8nClientError as e:
            return json.dumps({"error": str(e), "status": e.status_code, "body": e.body})

    return json.dumps({"error": f"unknown tool {name}"})


def _to_openai_messages(
    system: str,
    incoming: list[ChatMessageIn],
    workflow_id: str | None,
    workflow_json: str | None,
) -> list[dict[str, Any]]:
    extra = ""
    if workflow_id:
        extra += f"\n\n[Editor context] current workflow_id: {workflow_id}\n"
    if workflow_json and workflow_json.strip():
        wj = workflow_json.strip()
        if len(wj) > 120_000:
            wj = wj[:120_000] + "\n…[truncated]…"
        extra += f"\n\n[Editor workflow JSON]\n```json\n{wj}\n```\n"

    msgs: list[dict[str, Any]] = [{"role": "system", "content": system + extra}]
    for m in incoming:
        if m.role == "assistant":
            msgs.append({"role": "assistant", "content": m.content or ""})
        elif m.role == "user":
            msgs.append({"role": "user", "content": m.content or ""})
    return msgs


async def run_chat(req: ChatRequest) -> dict[str, Any]:
    if not req.messages:
        raise RuntimeError("messages must not be empty")

    llm = await multi_config.resolve_active_llm()
    if not llm:
        raise RuntimeError(
            "AI is not configured. With PostgreSQL enabled, create an LLM profile and set it active. "
            "Otherwise set AZURE_OPENAI_* or OPENAI_API_KEY in the environment."
        )

    from openai import AsyncAzureOpenAI, AsyncOpenAI

    if llm.provider == "azure_openai":
        client: Any = AsyncAzureOpenAI(
            azure_endpoint=str(llm.azure_endpoint),
            api_key=str(llm.api_key),
            api_version=str(llm.api_version or "2024-08-01-preview"),
        )
        model = str(llm.azure_deployment)
    else:
        client = AsyncOpenAI(api_key=str(llm.api_key), base_url=str(llm.base_url))
        model = str(llm.model or "gpt-4o-mini")

    system = _system_prompt()
    messages = _to_openai_messages(system, req.messages, req.workflow_id, req.workflow_json)

    temperature = float(llm.temperature)
    max_tokens = int(llm.max_tokens)

    rounds = 0
    last_assistant_text = ""

    while rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": TOOLS,
            "tool_choice": "auto",
        }
        try:
            resp = await client.chat.completions.create(**kwargs)
        except TypeError:
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = max_tokens
            resp = await client.chat.completions.create(**kwargs)

        choice = resp.choices[0]
        msg = choice.message
        assistant_content = msg.content or ""
        tool_calls = getattr(msg, "tool_calls", None) or []

        if assistant_content:
            last_assistant_text = assistant_content

        if not tool_calls:
            return {
                "answer_markdown": assistant_content,
                "provider": llm.provider,
                "model": model,
                "finish_reason": choice.finish_reason,
            }

        messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            result = await _run_tool(name, tc.function.arguments or "{}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

    return {
        "answer_markdown": last_assistant_text or "Tool loop limit reached.",
        "provider": llm.provider,
        "model": model,
        "finish_reason": "tool_loop_limit",
    }
