from __future__ import annotations

import pytest
from google.genai import types

from app.agent.controller import AgentController, AgentError
from app.agent.events import (
    AssistantEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.mcp_client.client import MCPConnectionError, ToolResult
from tests.conftest import (
    FakeMCP,
    ScriptedLLM,
    function_call,
    make_tool,
    text_response,
    tool_call_response,
)


def _function_responses(contents: list[types.Content]) -> list[types.FunctionResponse]:
    responses: list[types.FunctionResponse] = []
    for content in contents:
        for part in content.parts or []:
            if part.function_response is not None:
                responses.append(part.function_response)
    return responses


@pytest.mark.asyncio
async def test_normal_conversation_without_tools() -> None:
    llm = ScriptedLLM([text_response("Hello there.")])
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)
    events = []

    answer = await agent.send_message("Hi", events.append)

    assert answer == "Hello there."
    assert mcp.connect_count == 1
    assert mcp.calls == []
    assert [c.role for c in agent.history] == ["user", "model"]
    assert agent.history[0].parts[0].text == "Hi"
    assert agent.history[1].parts[0].text == "Hello there."
    assert any(isinstance(event, AssistantEvent) for event in events)
    assert any(isinstance(event, StatusEvent) for event in events)


@pytest.mark.asyncio
async def test_single_tool_invocation() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(
                function_call("echo", {"text": "hello"}, call_id="call-1")
            ),
            text_response("The tool said hello."),
        ]
    )
    mcp = FakeMCP(results={"echo": ToolResult("echo result for {'text': 'hello'}")})
    agent = AgentController(llm, mcp)
    events = []

    answer = await agent.send_message("Use the echo tool", events.append)

    assert answer == "The tool said hello."
    assert len(mcp.calls) == 1
    assert mcp.calls[0][0].name == "echo"
    assert mcp.calls[0][1] == {"text": "hello"}

    # user, model(function call), user(function response), model(final)
    assert [c.role for c in agent.history] == ["user", "model", "user", "model"]
    responses = _function_responses(list(agent.history))
    assert len(responses) == 1
    assert responses[0].name == "echo"
    assert "echo result" in responses[0].response["result"]
    assert responses[0].id == "call-1"

    assert any(
        isinstance(event, ToolCallEvent) and event.name == "echo" for event in events
    )
    assert any(
        isinstance(event, ToolResultEvent)
        and event.name == "echo"
        and not event.is_error
        for event in events
    )


@pytest.mark.asyncio
async def test_multiple_sequential_tool_invocations() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("echo", {"text": "one"})),
            tool_call_response(function_call("echo", {"text": "two"})),
            text_response("Both calls finished."),
        ]
    )
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)

    answer = await agent.send_message("Call it twice")

    assert answer == "Both calls finished."
    assert [args["text"] for _, args in mcp.calls] == ["one", "two"]
    assert len(llm.requests) == 3
    # Each tool round adds a model turn and a function-response turn.
    assert [c.role for c in agent.history] == [
        "user",
        "model",
        "user",
        "model",
        "user",
        "model",
    ]


@pytest.mark.asyncio
async def test_multiple_function_calls_in_one_response() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(
                function_call("echo", {"text": "first"}, call_id="a"),
                function_call("echo", {"text": "second"}, call_id="b"),
            ),
            text_response("Both were executed."),
        ]
    )
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)

    answer = await agent.send_message("Do two things at once")

    assert answer == "Both were executed."
    assert [args["text"] for _, args in mcp.calls] == ["first", "second"]

    function_turn = agent.history[2]
    assert function_turn.role == "user"
    assert len(function_turn.parts) == 2
    assert function_turn.parts[0].function_response.id == "a"
    assert function_turn.parts[1].function_response.id == "b"


@pytest.mark.asyncio
async def test_tool_execution_failure_is_returned_to_gemini() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("echo", {"text": "boom"})),
            text_response("The tool failed; here is why."),
        ]
    )
    mcp = FakeMCP(failures={"echo": RuntimeError("server exploded")})
    agent = AgentController(llm, mcp)
    events = []

    answer = await agent.send_message("Try it", events.append)

    assert answer == "The tool failed; here is why."
    response = _function_responses(list(agent.history))[0]
    assert "server exploded" in response.response["error"]
    assert any(
        isinstance(event, ToolResultEvent) and event.is_error for event in events
    )


@pytest.mark.asyncio
async def test_unavailable_mcp_server_fails_cleanly() -> None:
    llm = ScriptedLLM([text_response("should not be reached")])
    mcp = FakeMCP(connect_error=MCPConnectionError("no server"))
    agent = AgentController(llm, mcp)

    with pytest.raises(MCPConnectionError, match="no server"):
        await agent.send_message("Hi")

    assert agent.history == ()
    assert llm.requests == []


@pytest.mark.asyncio
async def test_failed_first_gemini_request_rolls_back_user_message() -> None:
    llm = ScriptedLLM([RuntimeError("network down")])
    agent = AgentController(llm, FakeMCP())

    with pytest.raises(RuntimeError, match="network down"):
        await agent.send_message("This should not stay in history")

    assert agent.history == ()


@pytest.mark.asyncio
async def test_iteration_limit_is_enforced() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("echo", {"text": "1"})),
            tool_call_response(function_call("echo", {"text": "2"})),
            tool_call_response(function_call("echo", {"text": "3"})),
        ]
    )
    mcp = FakeMCP()
    agent = AgentController(llm, mcp, max_iterations=2)

    with pytest.raises(Exception, match="Stopped after 2"):
        await agent.send_message("Loop forever")

    assert len(mcp.calls) == 2
    assert len(llm.requests) == 2


@pytest.mark.asyncio
async def test_conversation_context_preserved_across_messages() -> None:
    llm = ScriptedLLM([text_response("First answer."), text_response("Second answer.")])
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)

    await agent.send_message("First question")
    await agent.send_message("Second question")

    assert len(llm.requests) == 2
    second_request = llm.requests[1]
    assert [content.role for content in second_request] == ["user", "model", "user"]
    assert second_request[0].parts[0].text == "First question"
    assert second_request[1].parts[0].text == "First answer."
    assert second_request[2].parts[0].text == "Second question"
    assert mcp.connect_count == 1  # connection is reused


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected_without_execution() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("made_up_tool", {"x": 1})),
            text_response("I cannot call that."),
        ]
    )
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)

    await agent.send_message("Invent a tool")

    assert mcp.calls == []
    response = _function_responses(list(agent.history))[0]
    assert "Unknown tool" in response.response["error"]


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_without_execution() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("echo", {"text": 123})),
            text_response("I need to fix the arguments."),
        ]
    )
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)

    await agent.send_message("Pass a number")

    assert mcp.calls == []
    response = _function_responses(list(agent.history))[0]
    assert "Invalid arguments" in response.response["error"]


@pytest.mark.asyncio
async def test_malformed_function_call_arguments_are_rejected() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("echo", "{not json")),
            text_response("The arguments were malformed."),
        ]
    )
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)

    await agent.send_message("Break the arguments")

    assert mcp.calls == []
    response = _function_responses(list(agent.history))[0]
    assert "not valid JSON" in response.response["error"]


@pytest.mark.asyncio
async def test_user_can_deny_a_tool_call() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("echo", {"text": "danger"})),
            text_response("I did not run it."),
        ]
    )
    mcp = FakeMCP()
    confirmations: list[tuple[str, dict]] = []

    def deny(name: str, arguments: dict) -> bool:
        confirmations.append((name, arguments))
        return False

    agent = AgentController(llm, mcp, confirm_tool=deny)

    await agent.send_message("Run the tool")

    assert confirmations == [("echo", {"text": "danger"})]
    assert mcp.calls == []
    response = _function_responses(list(agent.history))[0]
    assert "denied" in response.response["error"]


@pytest.mark.asyncio
async def test_empty_gemini_response_raises_clear_error() -> None:
    empty = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[]))]
    )
    llm = ScriptedLLM([empty])
    agent = AgentController(llm, FakeMCP())

    with pytest.raises(AgentError, match="neither text nor a function call"):
        await agent.send_message("Hello?")


@pytest.mark.asyncio
async def test_large_tool_result_is_truncated_before_gemini() -> None:
    llm = ScriptedLLM(
        [
            tool_call_response(function_call("echo", {"text": "x"})),
            text_response("Done."),
        ]
    )
    mcp = FakeMCP(results={"echo": ToolResult("A" * 1000)})
    agent = AgentController(llm, mcp, max_tool_result_chars=100)

    await agent.send_message("Give me a lot")

    response = _function_responses(list(agent.history))[0]
    assert len(response.response["result"]) < 200
    assert "truncated 900 characters" in response.response["result"]


@pytest.mark.asyncio
async def test_shutdown_closes_mcp_and_llm() -> None:
    llm = ScriptedLLM([text_response("hi")])
    mcp = FakeMCP()
    agent = AgentController(llm, mcp)

    await agent.start()
    await agent.send_message("hello")
    await agent.shutdown()

    assert mcp.close_count == 1
    assert llm.closed is True


@pytest.mark.asyncio
async def test_history_snapshot_can_be_restored() -> None:
    llm = ScriptedLLM([text_response("First answer."), text_response("Second answer.")])
    agent = AgentController(llm, FakeMCP())

    await agent.send_message("First question")
    snapshot = agent.snapshot_history()
    await agent.send_message("Second question")
    assert len(agent.history) == 4

    agent.restore_history(snapshot)
    assert [content.parts[0].text for content in agent.history] == [
        "First question",
        "First answer.",
    ]
    assert agent.snapshot_history() == snapshot
    assert agent.snapshot_history()[0] is not snapshot[0]

    agent.restore_history(None)
    assert agent.history == ()


def test_model_can_be_changed_between_requests() -> None:
    agent = AgentController(ScriptedLLM([text_response("ok")]), FakeMCP())

    assert agent.model == "scripted-model"
    agent.set_model("another-model")
    assert agent.model == "another-model"

    with pytest.raises(AgentError, match="cannot be empty"):
        agent.set_model("  ")


def test_provider_can_be_changed_between_requests() -> None:
    first = ScriptedLLM([text_response("ok")])
    second = ScriptedLLM([text_response("ok")])
    created: list[tuple[str, str]] = []

    def factory(provider: str, model: str):
        created.append((provider, model))
        second.model = model
        return second

    agent = AgentController(first, FakeMCP(), llm_factory=factory)

    agent.set_provider("openrouter", "openrouter-model")

    assert created == [("openrouter", "openrouter-model")]
    assert agent.model == "openrouter-model"

    with pytest.raises(AgentError, match="cannot be empty"):
        agent.set_provider("openrouter", "  ")


def test_provider_cannot_be_changed_without_a_factory() -> None:
    agent = AgentController(ScriptedLLM([text_response("ok")]), FakeMCP())

    with pytest.raises(AgentError, match="does not support"):
        agent.set_provider("openrouter", "some-model")


@pytest.mark.asyncio
async def test_switching_provider_closes_the_retired_client() -> None:
    first = ScriptedLLM([text_response("ok")])
    second = ScriptedLLM([text_response("ok")])
    agent = AgentController(
        first, FakeMCP(), llm_factory=lambda _provider, _model: second
    )

    await agent.start()
    agent.set_provider("openrouter", "openrouter-model")
    await agent.start()

    assert first.closed is True
    assert second.closed is False


@pytest.mark.asyncio
async def test_only_discovered_tools_are_exposed() -> None:
    llm = ScriptedLLM([text_response("ok")])
    mcp = FakeMCP(tools=[make_tool("alpha"), make_tool("beta")])
    agent = AgentController(llm, mcp)

    await agent.send_message("What tools exist?")

    tool_set = llm.tool_sets[0]
    assert {tool.name for tool in tool_set.mcp_tools} == {"alpha", "beta"}
    assert len(tool_set.declarations) == 2
