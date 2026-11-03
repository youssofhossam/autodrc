from __future__ import annotations

from typing import Any

import pytest
from google.genai import types

from app.llm.gemini import GeminiClient, GeminiError
from app.llm.schema import build_tool_set


class _FakeModels:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(role="model", parts=[types.Part(text="ok")])
                )
            ]
        )


class _FakeAio:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


class _FakeClient:
    def __init__(self, models: _FakeModels) -> None:
        self.aio = _FakeAio(models)

    async def aclose(self) -> None:
        pass


def _make_client(
    error: BaseException | None = None,
) -> tuple[GeminiClient, _FakeModels]:
    client = GeminiClient(api_key="test-key", model="gemini-test")
    models = _FakeModels(error)
    client._client = _FakeClient(models)  # type: ignore[assignment]
    return client, models


@pytest.mark.asyncio
async def test_generate_disables_automatic_function_calling() -> None:
    client, models = _make_client()
    tool_set = build_tool_set([])

    response = await client.generate([], tool_set)

    assert response.candidates[0].content.parts[0].text == "ok"
    config = models.calls[0]["config"]
    assert config.automatic_function_calling.disable is True
    assert config.tools is None
    assert models.calls[0]["model"] == "gemini-test"


@pytest.mark.asyncio
async def test_generate_passes_tool_declarations() -> None:
    from tests.conftest import make_tool

    client, models = _make_client()
    tool_set = build_tool_set([make_tool("echo")])

    await client.generate([], tool_set)

    config = models.calls[0]["config"]
    assert config.tools is not None
    assert config.tools[0].function_declarations[0].name == "echo"


@pytest.mark.asyncio
async def test_gemini_errors_are_redacted() -> None:
    secret = "AIza" + "x" * 30
    client, _ = _make_client(RuntimeError(f"request failed for {secret}"))

    with pytest.raises(GeminiError) as raised:
        await client.generate([], build_tool_set([]))

    assert secret not in str(raised.value)
    assert "[redacted]" in str(raised.value)
    assert len(str(raised.value)) < 5_000


def test_empty_api_key_is_rejected() -> None:
    with pytest.raises(GeminiError, match="GEMINI_API_KEY"):
        GeminiClient(api_key="", model="gemini-test")


def test_empty_model_is_rejected() -> None:
    with pytest.raises(GeminiError, match="GEMINI_MODEL"):
        GeminiClient(api_key="key", model="")
