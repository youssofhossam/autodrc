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


def test_confirmation_can_be_disabled() -> None:
    settings = Settings.from_env(_base_env(MCP_CONFIRM_TOOL_CALLS="false"))

    assert settings.confirm_tool_calls is False
