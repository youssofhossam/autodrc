"""Build an LLM client for a provider."""

from __future__ import annotations

from app.config import PROVIDERS, Settings
from app.llm.gemini import GeminiClient
from app.llm.openrouter import OpenRouterClient

LLMClient = GeminiClient | OpenRouterClient


def create_llm(
    settings: Settings,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> LLMClient:
    """Return a client for `provider`, defaulting to LLM_PROVIDER."""

    name = (provider or settings.llm_provider).strip().lower()
    if name not in PROVIDERS:
        choices = ", ".join(PROVIDERS)
        raise ValueError(f"Unsupported LLM provider '{name}'. Use one of: {choices}.")

    chosen_model = (model or "").strip() or settings.model_for(name)

    if name == "openrouter":
        return OpenRouterClient(
            api_key=settings.openrouter_api_key,
            model=chosen_model,
            base_url=settings.openrouter_base_url,
            system_instruction=settings.system_instruction,
            temperature=settings.temperature,
            site_url=settings.openrouter_site_url,
            app_name=settings.openrouter_app_name,
        )

    return GeminiClient(
        api_key=settings.gemini_api_key,
        model=chosen_model,
        system_instruction=settings.system_instruction,
        temperature=settings.temperature,
    )
