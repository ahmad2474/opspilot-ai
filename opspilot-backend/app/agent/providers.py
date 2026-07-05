"""Maps a provider name to a configured Agents-SDK Model instance.

Groq, Gemini, and NVIDIA NIM all speak the Chat Completions format, not
OpenAI's newer Responses API — Agent(model="some-string") assumes Responses
API under the hood and will fail against these. Wrapping each client in
OpenAIChatCompletionsModel forces Chat-Completions-style calls regardless
of which provider is behind the base_url.

Note on retries: the underlying `openai` client already retries 429s and
5xxs with exponential backoff by default (max_retries=2) before this code
even sees an exception. The provider fallback below is the *next* layer:
what happens when a provider is exhausted or unconfigured, not the first
line of defense against a single rate-limit blip.
"""
from __future__ import annotations

from agents import OpenAIChatCompletionsModel
from agents.models.interface import Model
from openai import AsyncOpenAI

from app.core.config import LLMProviderName, Settings, get_settings


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a provider is requested but has no API key set."""


def build_model(provider: LLMProviderName, settings: Settings | None = None) -> Model:
    settings = settings or get_settings()

    provider_config: dict[LLMProviderName, tuple[str | None, str, str]] = {
        "groq": (settings.groq_api_key, settings.groq_base_url, settings.groq_model),
        "gemini": (settings.gemini_api_key, settings.gemini_base_url, settings.gemini_model),
        "nvidia": (settings.nvidia_api_key, settings.nvidia_base_url, settings.nvidia_model),
    }

    api_key, base_url, model_name = provider_config[provider]
    if not api_key:
        raise ProviderNotConfiguredError(f"No API key set for provider '{provider}'")

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)
