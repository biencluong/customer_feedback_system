"""Azure OpenAI chat client used by the agent stages.

Wraps `openai.AzureOpenAI` with the small surface the pipeline needs:
structured outputs (via function calling) and a tool-calling loop.
Credentials come from environment variables — see `.env.example`.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Type, TypeVar

from openai import AzureOpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_azure_client(
    *,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    api_version: Optional[str] = None,
    timeout: float = 30.0,
    max_retries: int = 2,
) -> AzureOpenAI:
    return AzureOpenAI(
        api_key=api_key or _require_env("AZURE_OPENAI_API_KEY"),
        azure_endpoint=(endpoint or _require_env("AZURE_OPENAI_ENDPOINT")).rstrip("/"),
        api_version=api_version or os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        timeout=timeout,
        max_retries=max_retries,
    )


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
    # OpenAI function parameters should not carry Pydantic's $defs-only quirks if avoidable;
    # model_json_schema is fine for our simple tool schemas.
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
    def __init__(self, llm: "AzureChatModel", schema: Type[T]):
        self.llm = llm
        self.schema = schema

    def invoke(self, messages: List[Any]) -> T:
        tool = _schema_to_openai_tool(self.schema)
        response = self.llm.client.chat.completions.create(
            model=self.llm.deployment,
            messages=[_lc_message_to_openai(m) for m in messages],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": self.schema.__name__}},
            temperature=self.llm.temperature,
        )
        choice = response.choices[0].message
        if not choice.tool_calls:
            raise RuntimeError(f"Azure OpenAI returned no structured tool call for {self.schema.__name__}")
        args = choice.tool_calls[0].function.arguments
        data = json.loads(args) if isinstance(args, str) else args
        return self.schema.model_validate(data)


class _ToolBoundModel:
    def __init__(self, llm: "AzureChatModel", tools: List[Any]):
        self.llm = llm
        self.tools = tools
        self._openai_tools = [_tool_to_openai(t) for t in tools]

    def invoke(self, messages: List[Any]) -> _AIMessage:
        kwargs: Dict[str, Any] = {
            "model": self.llm.deployment,
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


class AzureChatModel:
    """Thin adapter: AzureOpenAI SDK underneath, LangChain-compatible invoke helpers on top."""

    def __init__(
        self,
        client: AzureOpenAI,
        deployment: str,
        temperature: float = 0.0,
    ):
        self.client = client
        self.deployment = deployment
        self.temperature = temperature

    def with_structured_output(self, schema: Type[T], method: str = "function_calling") -> _StructuredRunner:
        return _StructuredRunner(self, schema)

    def bind_tools(self, tools: List[Any]) -> _ToolBoundModel:
        return _ToolBoundModel(self, tools)


def azure_credentials_configured() -> bool:
    return bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"))
