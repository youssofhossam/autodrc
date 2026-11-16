from __future__ import annotations

import pytest

from app.config import ConfigError, Settings


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "GEMINI_API_KEY": "test-key",
        "GEMINI_MODEL": "gemini-test",
        "MCP_CONFIG_PATH": "servers.json",
    }
    env.update(overrides)
    return env


def test_defaults() -> None:
    settings = Settings.from_env(_base_env())

    assert settings.gemini_api_key == "test-key"
    assert settings.gemini_model == "gemini-test"
    assert settings.max_tool_iterations == 8
    assert settings.max_tool_result_chars == 20_000
    assert settings.confirm_tool_calls is True
    assert settings.temperature is None
    assert settings.system_instruction


def test_missing_api_key_is_clear() -> None:
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        Settings.from_env({**_base_env(), "GEMINI_API_KEY": ""})


def test_missing_model_is_clear() -> None:
    with pytest.raises(ConfigError, match="GEMINI_MODEL"):
        Settings.from_env({**_base_env(), "GEMINI_MODEL": ""})


def test_invalid_temperature() -> None:
    with pytest.raises(ConfigError, match="between 0.0 and 2.0"):
        Settings.from_env(_base_env(GEMINI_TEMPERATURE="9"))


def test_invalid_boolean() -> None:
    with pytest.raises(ConfigError, match="true or false"):
        Settings.from_env(_base_env(MCP_CONFIRM_TOOL_CALLS="maybe"))


def test_system_instruction_prefers_the_generic_variable() -> None:
    assert (
        Settings.from_env(
            _base_env(
                LLM_SYSTEM_INSTRUCTION="generic",
                GEMINI_SYSTEM_INSTRUCTION="legacy",
            )
        ).system_instruction
        == "generic"
    )
    assert (
        Settings.from_env(_base_env(GEMINI_SYSTEM_INSTRUCTION="legacy")).system_instruction
        == "legacy"
    )


def test_confirmation_can_be_disabled() -> None:
    settings = Settings.from_env(_base_env(MCP_CONFIRM_TOOL_CALLS="false"))

    assert settings.confirm_tool_calls is False


def _openrouter_env(**overrides: str) -> dict[str, str]:
    env = {
        "LLM_PROVIDER": "openrouter",
        "OPENROUTER_API_KEY": "sk-or-v1-test",
        "OPENROUTER_MODEL": "google/gemini-2.5-flash",
        "MCP_CONFIG_PATH": "servers.json",
    }
    env.update(overrides)
    return env


def test_default_provider_is_gemini() -> None:
    settings = Settings.from_env(_base_env())

    assert settings.llm_provider == "gemini"
    assert settings.model == "gemini-test"
    assert settings.api_key_configured is True


def test_provider_aliases_are_accepted() -> None:
    settings = Settings.from_env(_base_env(LLM_PROVIDER="Google"))

    assert settings.llm_provider == "gemini"


def test_invalid_provider_is_clear() -> None:
    with pytest.raises(ConfigError, match="LLM_PROVIDER"):
        Settings.from_env(_base_env(LLM_PROVIDER="anthropic"))


def test_openrouter_does_not_require_gemini_settings() -> None:
    settings = Settings.from_env(_openrouter_env())

    assert settings.llm_provider == "openrouter"
    assert settings.model == "google/gemini-2.5-flash"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openrouter_app_name == "autodrc"
    assert settings.api_key_configured is True


def test_openrouter_requires_key() -> None:
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        Settings.from_env({**_openrouter_env(), "OPENROUTER_API_KEY": ""})


def test_openrouter_requires_model() -> None:
    with pytest.raises(ConfigError, match="OPENROUTER_MODEL"):
        Settings.from_env({**_openrouter_env(), "OPENROUTER_MODEL": ""})


def test_openrouter_base_url_is_stripped() -> None:
    settings = Settings.from_env(
        _openrouter_env(OPENROUTER_BASE_URL="https://example.com/api/")
    )

    assert settings.openrouter_base_url == "https://example.com/api"


def test_llm_temperature_overrides_provider_specific_value() -> None:
    settings = Settings.from_env(
        _base_env(LLM_TEMPERATURE="0.4", GEMINI_TEMPERATURE="0.9")
    )

    assert settings.temperature == 0.4


def test_provider_specific_temperature_is_a_fallback() -> None:
    assert Settings.from_env(_base_env(GEMINI_TEMPERATURE="0.3")).temperature == 0.3
    assert (
        Settings.from_env(_openrouter_env(OPENROUTER_TEMPERATURE="0.6")).temperature
        == 0.6
    )


def test_factory_selects_the_configured_client() -> None:
    from app.llm import GeminiClient, OpenRouterClient, create_llm

    assert isinstance(create_llm(Settings.from_env(_base_env())), GeminiClient)

    openrouter = create_llm(Settings.from_env(_openrouter_env()))
    assert isinstance(openrouter, OpenRouterClient)
    assert openrouter.model == "google/gemini-2.5-flash"


def test_factory_can_build_another_configured_provider() -> None:
    from app.llm import GeminiClient, OpenRouterClient, create_llm

    settings = Settings.from_env(
        _base_env(
            OPENROUTER_API_KEY="sk-or-v1-test",
            OPENROUTER_MODEL="openrouter-default",
        )
    )
    assert settings.model_for("openrouter") == "openrouter-default"
    assert settings.api_key_configured_for("openrouter") is True

    client = create_llm(settings, provider="openrouter", model="openrouter-chosen")
    assert isinstance(client, OpenRouterClient)
    assert client.model == "openrouter-chosen"

    assert isinstance(
        create_llm(settings, provider="gemini", model="gemini-chosen"),
        GeminiClient,
    )
