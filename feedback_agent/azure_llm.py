"""OpenAI chat client used by the agent stages.

Supports either:
  - Azure OpenAI (`AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`), or
  - public OpenAI (`OPENAI_API_KEY`)

Wraps the OpenAI SDK with the small surface the pipeline needs: structured outputs
(via function calling) and a tool-calling loop. See `.env.example`.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from openai import AzureOpenAI, OpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
OpenAIClient = Union[OpenAI, AzureOpenAI]


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _require_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def llm_provider() -> str:
    """Return 'azure', 'openai', or 'none' based on which credentials are present."""
    if _env("AZURE_OPENAI_API_KEY") and _env("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    if _env("OPENAI_API_KEY"):
        return "openai"
    return "none"


def llm_credentials_configured() -> bool:
    return llm_provider() != "none"


# Back-compat alias used by older call sites / docs snippets.
azure_credentials_configured = llm_credentials_configured


def resolve_model_name() -> str:
    if llm_provider() == "azure":
        return _env("AZURE_OPENAI_DEPLOYMENT") or _env("OPENAI_MODEL") or "gpt-4o-mini"
    return _env("OPENAI_MODEL") or _env("AZURE_OPENAI_DEPLOYMENT") or "gpt-4o-mini"


def build_client(
    *,
    timeout: float = 30.0,
    max_retries: int = 2,
    api_version: Optional[str] = None,
) -> OpenAIClient:
    provider = llm_provider()
    if provider == "azure":
        return AzureOpenAI(
            api_key=_require_env("AZURE_OPENAI_API_KEY"),
            azure_endpoint=_require_env("AZURE_OPENAI_ENDPOINT").rstrip("/"),
            api_version=api_version or _env("AZURE_OPENAI_API_VERSION") or "2024-08-01-preview",
            timeout=timeout,
            max_retries=max_retries,
        )
    if provider == "openai":
        kwargs: Dict[str, Any] = {
            "api_key": _require_env("OPENAI_API_KEY"),
            "timeout": timeout,
            "max_retries": max_retries,
        }
        # Optional: custom base URL (OpenAI-compatible proxies, etc.)
        base_url = _env("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        return OpenAI(**kwargs)
    raise RuntimeError(
        "No LLM credentials found. Set OPENAI_API_KEY, or AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT."
    )


# Older name kept so existing imports keep working.
build_azure_client = build_client


def _lc_message_to_openai(msg: Any) -> Dict[str, Any]:
    """Convert a LangChain message (or already-OpenAI dict) to the Chat Completions format."""
    if isinstance(msg, dict):
        return msg

    role_map = {
        "system": "system",
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
    }
    msg_type = getattr(msg, "type", None)
    role = role_map.get(msg_type)
    if role is None:
        raise TypeError(f"Unsupported message type: {type(msg)!r}")

    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
        }

    payload: Dict[str, Any] = {
        "role": role,
        "content": msg.content if isinstance(msg.content, str) else (msg.content or ""),
    }
    if role == "assistant" and getattr(msg, "tool_calls", None):
        payload["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["args"] if isinstance(tc["args"], str) else json.dumps(tc["args"]),
                },
            }
            for tc in msg.tool_calls
        ]
        if not payload["content"]:
            payload["content"] = None
    return payload


def _tool_to_openai(tool: Any) -> Dict[str, Any]:
    schema = tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


def _schema_to_openai_tool(schema: Type[BaseModel]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema.__name__,
            "description": (schema.__doc__ or f"Return a {schema.__name__} object.").strip(),
            "parameters": schema.model_json_schema(),
        },
    }


class _AIMessage:
    """Minimal stand-in for LangChain AIMessage so the context agent loop stays unchanged."""

    def __init__(self, content: Optional[str], tool_calls: Optional[List[dict]] = None):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.type = "ai"


class _StructuredRunner:
    def __init__(self, llm: "ChatModel", schema: Type[T]):
        self.llm = llm
        self.schema = schema

    def invoke(self, messages: List[Any]) -> T:
        tool = _schema_to_openai_tool(self.schema)
        response = self.llm.client.chat.completions.create(
            model=self.llm.model,
            messages=[_lc_message_to_openai(m) for m in messages],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": self.schema.__name__}},
            temperature=self.llm.temperature,
        )
        choice = response.choices[0].message
        if not choice.tool_calls:
            raise RuntimeError(f"LLM returned no structured tool call for {self.schema.__name__}")
        args = choice.tool_calls[0].function.arguments
        data = json.loads(args) if isinstance(args, str) else args
        return self.schema.model_validate(data)


class _ToolBoundModel:
    def __init__(self, llm: "ChatModel", tools: List[Any]):
        self.llm = llm
        self.tools = tools
        self._openai_tools = [_tool_to_openai(t) for t in tools]

    def invoke(self, messages: List[Any]) -> _AIMessage:
        kwargs: Dict[str, Any] = {
            "model": self.llm.model,
            "messages": [_lc_message_to_openai(m) for m in messages],
            "temperature": self.llm.temperature,
        }
        if self._openai_tools:
            kwargs["tools"] = self._openai_tools
            kwargs["tool_choice"] = "auto"
        response = self.llm.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        tool_calls = []
        for tc in message.tool_calls or []:
            args = tc.function.arguments
            parsed = json.loads(args) if isinstance(args, str) else (args or {})
            tool_calls.append({"name": tc.function.name, "args": parsed, "id": tc.id, "type": "tool_call"})
        return _AIMessage(content=message.content, tool_calls=tool_calls)


class ChatModel:
    """Thin adapter: OpenAI/AzureOpenAI SDK underneath, LangChain-compatible invoke helpers on top."""

    def __init__(
        self,
        client: OpenAIClient,
        model: str,
        temperature: float = 0.0,
    ):
        self.client = client
        self.model = model
        # Alias used by older call sites that passed `deployment=...`.
        self.deployment = model
        self.temperature = temperature

    def with_structured_output(self, schema: Type[T], method: str = "function_calling") -> _StructuredRunner:
        return _StructuredRunner(self, schema)

    def bind_tools(self, tools: List[Any]) -> _ToolBoundModel:
        return _ToolBoundModel(self, tools)


# Back-compat name.
AzureChatModel = ChatModel
