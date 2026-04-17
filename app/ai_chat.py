"""LLM chat with optional tools for n8n workflow get/update."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from .n8n_knowledge import N8N_KNOWLEDGE_PACK
from .n8n_client import N8nClientError, client_from_resolved
from .settings_store import resolved_connection

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


def _llm_config() -> tuple[str, dict[str, Any]]:
    """Returns (provider, kwargs for AsyncOpenAI client)."""
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


def ai_status() -> dict[str, Any]:
    provider, cfg = _llm_config()
    enabled = provider != "none"
    out: dict[str, Any] = {"enabled": enabled, "provider": provider}
    if provider == "azure_openai":
        ep = str(cfg.get("azure_endpoint", ""))
        out["endpoint"] = re.sub(r"https?://([^.]+)\.", r"https://***.", ep, count=1) if ep else ""
        out["deployment"] = cfg.get("azure_deployment", "")
    elif provider == "openai_compatible":
        out["base_url"] = cfg.get("base_url", "")
    return out


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

    base, key = resolved_connection()
    try:
        client = client_from_resolved(base, key)
    except N8nClientError as e:
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
        # Client may only send user/assistant history; ignore other roles for safety.
        if m.role == "assistant":
            msgs.append({"role": "assistant", "content": m.content or ""})
        elif m.role == "user":
            msgs.append({"role": "user", "content": m.content or ""})
    return msgs


async def run_chat(req: ChatRequest) -> dict[str, Any]:
    if not req.messages:
        raise RuntimeError("messages must not be empty")

    provider, cfg = _llm_config()
    if provider == "none":
        raise RuntimeError(
            "AI is not configured. Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT "
            "or OPENAI_API_KEY (optional OPENAI_BASE_URL)."
        )

    from openai import AsyncAzureOpenAI, AsyncOpenAI

    if provider == "azure_openai":
        client: Any = AsyncAzureOpenAI(
            azure_endpoint=str(cfg["azure_endpoint"]),
            api_key=str(cfg["api_key"]),
            api_version=str(cfg["api_version"]),
        )
        model = str(cfg["azure_deployment"])
    else:
        client = AsyncOpenAI(api_key=str(cfg["api_key"]), base_url=str(cfg["base_url"]))
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

    system = _system_prompt()
    messages = _to_openai_messages(system, req.messages, req.workflow_id, req.workflow_json)

    temperature = float(os.environ.get("N8N_EDITOR_AI_TEMPERATURE", "0.2"))
    max_tokens = int(os.environ.get("N8N_EDITOR_AI_MAX_TOKENS", "4096"))

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
                "provider": provider,
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
        "provider": provider,
        "model": model,
        "finish_reason": "tool_loop_limit",
    }
