from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import types
from openai import APIStatusError, OpenAIError

from app.llm.openrouter import OpenRouterClient, OpenRouterError
from tests.conftest import make_tool


class _FakeCompletions:
    def __init__(
        self, result: Any = None, error: BaseException | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class _FakeClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _make_client(
    result: Any = None, error: BaseException | None = None
) -> tuple[OpenRouterClient, _FakeCompletions]:
    client = OpenRouterClient(
        api_key="sk-or-v1-" + "a" * 32,
        model="google/gemini-2.5-flash",
        system_instruction="Be helpful.",
    )
    completions = _FakeCompletions(result, error)
    client._client = _FakeClient(completions)  # type: ignore[assignment]
    return client, completions


def _completion(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
) -> Any:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def _tool_call(
    name: str = "echo", arguments: str = '{"text": "hi"}', call_id: str = "call_1"
) -> Any:
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def _user(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


@pytest.mark.asyncio
async def test_text_completion_is_converted() -> None:
    client, completions = _make_client(_completion(content="hello"))

    response = await client.generate([_user("hi")], client.build_tools([]))

    assert response.candidates[0].content.parts[0].text == "hello"
    assert response.candidates[0].finish_reason == types.FinishReason.STOP
    request = completions.calls[0]
    assert request["model"] == "google/gemini-2.5-flash"
    assert request["messages"][0] == {"role": "system", "content": "Be helpful."}
    assert request["messages"][1] == {"role": "user", "content": "hi"}
    assert "tools" not in request


@pytest.mark.asyncio
async def test_tools_use_raw_mcp_json_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    client, completions = _make_client(_completion(content="ok"))
    tool_set = client.build_tools([make_tool("klink.find_tools", schema=schema)])

    await client.generate([], tool_set)

    definition = completions.calls[0]["tools"][0]
    assert definition["type"] == "function"
    assert definition["function"]["name"] == "klink_find_tools"
    assert definition["function"]["description"].startswith(
        "[MCP server: test-server]"
    )
    # OpenRouter accepts the schema unchanged, unlike Gemini.
    assert definition["function"]["parameters"] == schema
    assert completions.calls[0]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_tool_call_is_converted() -> None:
    client, _ = _make_client(
        _completion(tool_calls=[_tool_call()], finish_reason="tool_calls")
    )

    response = await client.generate([_user("hi")], client.build_tools([]))

    call = response.candidates[0].content.parts[0].function_call
    assert call.name == "echo"
    assert call.id == "call_1"
    assert call.args == {"text": "hi"}


@pytest.mark.asyncio
async def test_malformed_arguments_are_preserved_for_the_controller() -> None:
    client, _ = _make_client(
        _completion(
            tool_calls=[_tool_call(arguments="{not json")],
            finish_reason="tool_calls",
        )
    )

    response = await client.generate([], client.build_tools([]))

    call = response.candidates[0].content.parts[0].function_call
    assert call.args == "{not json"


@pytest.mark.asyncio
async def test_history_is_translated_to_openai_messages() -> None:
    client, completions = _make_client(_completion(content="done"))
    history: list[types.Content] = [
        _user("run echo"),
        types.Content(
            role="model",
            parts=[
                types.Part(text="Calling the tool."),
                types.Part(
                    function_call=types.FunctionCall(
                        name="echo", args={"text": "hi"}, id="call_1"
                    )
                ),
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="echo",
                        response={"result": "echo result"},
                        id="call_1",
                    )
                )
            ],
        ),
    ]

    await client.generate(history, client.build_tools([]))

    messages = completions.calls[0]["messages"]
    assert messages[1] == {"role": "user", "content": "run echo"}
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] == "Calling the tool."
    assert messages[2]["tool_calls"][0]["id"] == "call_1"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "echo"
    assert messages[2]["tool_calls"][0]["function"]["arguments"] == '{"text": "hi"}'
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "echo result",
    }


@pytest.mark.asyncio
async def test_tool_results_pair_by_order_when_ids_are_missing() -> None:
    client, completions = _make_client(_completion(content="done"))
    history = [
        _user("run echo"),
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="echo", args={"text": "hi"})
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="echo", response={"error": "denied"}
                    )
                )
            ],
        ),
    ]

    await client.generate(history, client.build_tools([]))

    messages = completions.calls[0]["messages"]
    synthetic_id = messages[2]["tool_calls"][0]["id"]
    assert synthetic_id
    assert messages[3]["tool_call_id"] == synthetic_id
    assert messages[3]["content"] == "Tool error: denied"


@pytest.mark.asyncio
async def test_multiple_tool_calls_and_results_are_kept_in_order() -> None:
    client, completions = _make_client(_completion(content="done"))
    history = [
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="one", args={}, id="a")
                ),
                types.Part(
                    function_call=types.FunctionCall(name="two", args={}, id="b")
                ),
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="one", response={"result": "1"}, id="a"
                    )
                ),
                types.Part(
                    function_response=types.FunctionResponse(
                        name="two", response={"result": "2"}, id="b"
                    )
                ),
            ],
        ),
    ]

    await client.generate(history, client.build_tools([]))

    messages = completions.calls[0]["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in messages[2:]] == ["a", "b"]


@pytest.mark.asyncio
async def test_thought_parts_are_not_replayed_as_text() -> None:
    client, completions = _make_client(_completion(content="done"))
    history = [
        types.Content(
            role="model",
            parts=[
                types.Part(text="internal reasoning", thought=True),
                types.Part(text="visible answer"),
            ],
        )
    ]

    await client.generate(history, client.build_tools([]))

    messages = completions.calls[0]["messages"]
    assert messages[1] == {"role": "assistant", "content": "visible answer"}


@pytest.mark.asyncio
async def test_empty_completion_raises_with_finish_reason() -> None:
    client, _ = _make_client(_completion(content=None, finish_reason="length"))

    with pytest.raises(OpenRouterError, match="finish reason: length"):
        await client.generate([_user("hi")], client.build_tools([]))


@pytest.mark.asyncio
async def test_status_errors_are_redacted_and_hinted() -> None:
    secret = "sk-or-v1-" + "b" * 32
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    error = APIStatusError(
        f"Unauthorized for {secret}",
        response=httpx.Response(401, request=request),
        body=None,
    )
    client, _ = _make_client(error=error)

    with pytest.raises(OpenRouterError) as raised:
        await client.generate([], client.build_tools([]))

    message = str(raised.value)
    assert secret not in message
    assert "[redacted]" in message
    assert "OPENROUTER_API_KEY" in message


@pytest.mark.asyncio
async def test_generic_sdk_errors_are_redacted() -> None:
    secret = "sk-or-v1-" + "c" * 32
    client, _ = _make_client(error=OpenAIError(f"failed for {secret}"))

    with pytest.raises(OpenRouterError) as raised:
        await client.generate([], client.build_tools([]))

    assert secret not in str(raised.value)
    assert "[redacted]" in str(raised.value)


def test_model_can_be_changed_and_empty_model_is_rejected() -> None:
    client, _ = _make_client()

    client.set_model("  anthropic/claude-sonnet-4  ")
    assert client.model == "anthropic/claude-sonnet-4"

    with pytest.raises(OpenRouterError, match="OPENROUTER_MODEL"):
        client.set_model("   ")


def test_missing_key_or_model_is_rejected() -> None:
    with pytest.raises(OpenRouterError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(api_key="", model="some/model")
    with pytest.raises(OpenRouterError, match="OPENROUTER_MODEL"):
        OpenRouterClient(api_key="sk-or-v1-key", model="")


@pytest.mark.asyncio
async def test_close_closes_the_sdk_client() -> None:
    client, _ = _make_client(_completion(content="ok"))
    fake = client._client

    await client.aclose()

    assert fake.closed is True


class _SequenceCompletions:
    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_agent_loop_executes_tools_through_openrouter() -> None:
    from app.agent.controller import AgentController
    from tests.conftest import FakeMCP

    client, _ = _make_client()
    completions = _SequenceCompletions(
        [
            _completion(tool_calls=[_tool_call()], finish_reason="tool_calls"),
            _completion(content="done"),
        ]
    )
    client._client = _FakeClient(completions)  # type: ignore[assignment]
    mcp = FakeMCP()
    agent = AgentController(client, mcp)

    result = await agent.send_message("please echo")

    assert result == "done"
    assert mcp.calls == [(mcp.tools[0], {"text": "hi"})]
    assert [content.role for content in agent.history] == [
        "user",
        "model",
        "user",
        "model",
    ]
    tool_message = completions.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert "echo result" in tool_message["content"]
