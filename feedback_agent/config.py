from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = ROOT_DIR / "data"
RUNS_DIR = ROOT_DIR / "runs"


def _default_model() -> str:
    from .azure_llm import resolve_model_name

    return resolve_model_name()


@dataclass
class Settings:
    # Model id (OpenAI) or Azure deployment name.
    model: str = field(default_factory=_default_model)
    temperature: float = 0.0
    request_timeout_s: float = 30.0
    # Retries on transient API errors (rate limits, 5xx, timeouts), handled by the OpenAI client.
    max_retries: int = 2
    # Hard cap on LLM turns in the context-gathering loop.
    max_agent_steps: int = 8
    # Classifications below this confidence are treated as ambiguous.
    ambiguity_threshold: float = 0.6
    api_version: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"))


def build_llm(settings: Settings):
    """Create the chat model for whichever provider credentials are configured."""
    from .azure_llm import ChatModel, build_client

    client = build_client(
        api_version=settings.api_version,
        timeout=settings.request_timeout_s,
        max_retries=settings.max_retries,
    )
    return ChatModel(client=client, model=settings.model, temperature=settings.temperature)
