from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DATA_DIR = ROOT_DIR / "data"
RUNS_DIR = ROOT_DIR / "runs"


@dataclass
class Settings:
    # Azure OpenAI deployment name (not necessarily the underlying model id).
    model: str = field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    )
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
    """Create the Azure OpenAI chat model. Raises if required Azure env vars are missing."""
    from .azure_llm import AzureChatModel, build_azure_client

    client = build_azure_client(
        api_version=settings.api_version,
        timeout=settings.request_timeout_s,
        max_retries=settings.max_retries,
    )
    return AzureChatModel(client=client, deployment=settings.model, temperature=settings.temperature)
