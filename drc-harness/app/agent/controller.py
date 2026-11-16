"""The agent controller: the manual model <-> MCP tool-calling loop."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import jsonschema
from google.genai import types

from app.agent.events import (
    AgentEvent,
    AssistantEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.llm.schema import ToolSet
from app.mcp_client.client import MCPTool, ToolResult

logger = logging.getLogger(__name__)

EventSink = Callable[[AgentEvent], None]
ConfirmationCallback = Callable[[str, dict[str, Any]], bool]
ContinueCallback = Callable[[int], bool]


class AgentError(RuntimeError):
    """The agent could not complete a turn."""


class IterationLimitError(AgentError):
    """The model kept requesting tools after the configured limit."""


class LLMClient(Protocol):
    def build_tools(self, mcp_tools: Sequence[MCPTool]) -> ToolSet: ...

    async def generate(
        self,
        contents: Sequence[types.Content],
        tool_set: ToolSet,
    ) -> types.GenerateContentResponse: ...

    async def aclose(self) -> None: ...


LLMFactory = Callable[[str, str], LLMClient]


class ToolProvider(Protocol):
    async def connect(self) -> None: ...

    async def list_tools(self) -> list[MCPTool]: ...

    async def call_tool(
        self, tool: MCPTool, arguments: dict[str, Any]
    ) -> ToolResult: ...

    async def close(self) -> None: ...


class AgentController:
    """Owns conversation history and runs the tool-calling loop explicitly."""

    def __init__(
        self,
        llm: LLMClient,
        mcp: ToolProvider,
        *,
        max_iterations: int = 1000,
        max_tool_result_chars: int = 20_000,
        confirm_tool: ConfirmationCallback | None = None,
        llm_factory: LLMFactory | None = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1.")
        if max_tool_result_chars < 100:
            raise ValueError("max_tool_result_chars must be at least 100.")
        self._llm = llm
        self._llm_factory = llm_factory
        self._retired_llms: list[LLMClient] = []
        self._mcp = mcp
        self._max_iterations = max_iterations
        self._max_tool_result_chars = max_tool_result_chars
        self._confirm_tool = confirm_tool
        self._continue_turn: ContinueCallback | None = None
        self._contents: list[types.Content] = []
        self._request_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._started = False
        self._tool_count = 0

    @property
    def history(self) -> tuple[types.Content, ...]:
        """Read-only view of the complete conversation, including tool turns."""

        return tuple(self._contents)

    @property
    def tool_count(self) -> int:
        """Number of tools discovered during the most recent turn."""

        return self._tool_count

    @property
    def model(self) -> str:
        return str(getattr(self._llm, "model", "") or "")

    def set_model(self, model: str) -> None:
        """Change the model used for subsequent turns."""

        name = model.strip()
        if not name:
            raise AgentError("The model name cannot be empty.")
        if self._request_lock.locked():
            raise AgentError("Cannot change the model while a request is running.")
        setter = getattr(self._llm, "set_model", None)
        if setter is None:
            raise AgentError("This client does not support changing models.")
        setter(name)

    def set_provider(self, provider: str, model: str) -> None:
        """Replace the LLM client so later turns use another provider.

        The conversation history is provider-neutral, so the current chat
        continues across the switch. The previous client is closed on the
        worker thread before the next request.
        """

        name = model.strip()
        if not name:
            raise AgentError("The model name cannot be empty.")
        if self._request_lock.locked():
            raise AgentError(
                "Cannot change the provider while a request is running."
            )
        if self._llm_factory is None:
            raise AgentError("This client does not support changing providers.")

        new_llm = self._llm_factory(provider.strip().lower(), name)
        self._retired_llms.append(self._llm)
        self._llm = new_llm

    async def _close_retired_llms(self) -> None:
        while self._retired_llms:
            client = self._retired_llms.pop()
            try:
                await client.aclose()
            except Exception:  # cleanup must not fail a turn
                logger.warning("Could not close a retired LLM client.", exc_info=True)

    def snapshot_history(self) -> tuple[types.Content, ...]:
        """Return a deep copy of the history that can be restored later.

        The GUI keeps one snapshot per conversation. Snapshots are opaque to the
        GUI; only the agent knows how to restore them.
        """

        return tuple(content.model_copy(deep=True) for content in self._contents)

    def restore_history(self, contents: Sequence[types.Content] | None) -> None:
        """Replace the conversation history with a previous snapshot."""

        if self._request_lock.locked():
            raise AgentError(
                "Cannot switch conversations while a request is running."
            )
        restored: list[types.Content] = []
        for content in contents or ():
            if not isinstance(content, types.Content):
                raise AgentError("The conversation snapshot is invalid.")
            restored.append(content.model_copy(deep=True))
        self._contents = restored

    async def start(self) -> None:
        """Connect to the MCP servers and keep them alive."""

        await self._close_retired_llms()
        if self._started and self._mcp_is_connected():
            return
        async with self._start_lock:
            if self._started and self._mcp_is_connected():
                return
            # A previous server process may have died. MCPClient.connect()
            # replaces stale connections.
            self._started = False
            await self._mcp.connect()
            self._started = True

    def _mcp_is_connected(self) -> bool:
        connected = getattr(self._mcp, "connected", None)
        return connected is not False

    def set_confirmation_callback(self, callback: ConfirmationCallback | None) -> None:
        """Install the GUI's confirmation dialog (or None to disable it)."""

        self._confirm_tool = callback

    def set_continue_callback(self, callback: ContinueCallback | None) -> None:
        """Install the callback asked before extending the step budget.

        The callback receives the number of model requests already made in the
        turn and returns True to grant another ``max_iterations`` steps.
        Without a callback the limit stays hard.
        """

        self._continue_turn = callback

    async def shutdown(self) -> None:
        """Wait for an in-flight turn, then close MCP and the LLM client."""

        async with self._request_lock:
            try:
                if self._started:
                    await self._mcp.close()
                    self._started = False
            finally:
                await self._llm.aclose()
                await self._close_retired_llms()

    def clear(self) -> None:
        """Forget the conversation. The GUI only calls this while idle."""

        if self._request_lock.locked():
            raise AgentError(
                "Cannot clear the conversation while a request is running."
            )
        self._contents.clear()

    async def send_message(
        self,
        user_text: str,
        on_event: EventSink | None = None,
    ) -> str:
        """Run one complete agentic turn and return the model's final text.

        The loop is deliberately visible here: ask the model, execute any
        requested tools, append their responses, ask again, repeat.
        """

        text = user_text.strip()
        if not text:
            raise AgentError("The user message is empty.")
        if self._request_lock.locked():
            raise AgentError("A request is already running.")

        emit = _safe_emitter(on_event)

        async with self._request_lock:
            await self.start()

            user_content = types.Content(role="user", parts=[types.Part(text=text)])
            self._contents.append(user_content)
            try:
                return await self._run_turn(emit)
            except BaseException:
                # If the turn failed before Gemini produced anything, do not
                # leave a dangling user message in the history.
                if self._contents and self._contents[-1] is user_content:
                    self._contents.pop()
                raise

    async def _run_turn(self, emit: Callable[[AgentEvent], None]) -> str:
        emit(StatusEvent("Discovering MCP tools..."))
        mcp_tools = await self._mcp.list_tools()
        self._tool_count = len(mcp_tools)
        tool_set = self._llm.build_tools(mcp_tools)

        steps = 0
        budget = self._max_iterations
        while True:
            while steps < budget:
                steps += 1
                emit(StatusEvent(f"Thinking (step {steps}/{budget})..."))
                response = await self._llm.generate(self._contents, tool_set)
                content = self._extract_content(response)
                self._contents.append(content)

                calls, text_parts = _parts_from_content(content)
                if text_parts:
                    emit(AssistantEvent("\n".join(text_parts)))

                if not calls:
                    final_text = "\n".join(text_parts).strip()
                    if not final_text:
                        raise AgentError(
                            "The model returned neither text nor a function call."
                        )
                    return final_text

                function_responses: list[types.Part] = []
                for call in calls:
                    function_responses.append(
                        await self._handle_function_call(call, tool_set, emit)
                    )

                # Function responses go back as a user-role turn, matching
                # Gemini's expected conversation format. The model turn above
                # was appended verbatim, preserving function calls and thought
                # signatures.
                self._contents.append(
                    types.Content(role="user", parts=function_responses)
                )

            if not await self._ask_continue(steps):
                raise IterationLimitError(
                    f"Stopped after {steps} model requests because the model "
                    "kept requesting tools."
                )
            budget += self._max_iterations

    async def _handle_function_call(
        self,
        call: types.FunctionCall,
        tool_set: ToolSet,
        emit: Callable[[AgentEvent], None],
    ) -> types.Part:
        """Validate and execute one function call, returning its response part."""

        requested_name = call.name or ""
        arguments, argument_error = _normalise_arguments(call.args)
        tool = tool_set.resolve(requested_name)

        if tool is None:
            message = (
                f"Unknown tool '{requested_name}'. Only tools discovered from "
                "the connected MCP servers can be called."
            )
            emit(ToolCallEvent(requested_name, "-", arguments))
            emit(ToolResultEvent(requested_name, message, is_error=True))
            return _function_response(requested_name, call.id, error=message)

        emit(ToolCallEvent(tool.name, tool.server, arguments))

        if argument_error is not None:
            emit(ToolResultEvent(tool.name, argument_error, is_error=True))
            return _function_response(requested_name, call.id, error=argument_error)

        validation_error = validate_tool_arguments(tool.input_schema, arguments)
        if validation_error is not None:
            emit(ToolResultEvent(tool.name, validation_error, is_error=True))
            return _function_response(requested_name, call.id, error=validation_error)

        if self._confirm_tool is not None:
            approved, confirmation_error = await self._ask_confirmation(
                tool.name, arguments
            )
            if not approved:
                message = confirmation_error or "The user denied this tool call."
                emit(ToolResultEvent(tool.name, message, is_error=True))
                return _function_response(requested_name, call.id, error=message)

        try:
            result = await self._mcp.call_tool(tool, arguments)
        except Exception as exc:  # noqa: BLE001 - sent back to Gemini to explain
            message = f"MCP tool execution failed: {type(exc).__name__}: {exc}"
            logger.warning("Tool '%s' failed: %s", tool.name, exc)
            emit(ToolResultEvent(tool.name, message, is_error=True))
            return _function_response(requested_name, call.id, error=message)

        text = _truncate(result.text, self._max_tool_result_chars)
        emit(ToolResultEvent(tool.name, text, is_error=result.is_error))
        if result.is_error:
            return _function_response(requested_name, call.id, error=text)
        return _function_response(requested_name, call.id, result=text)

    async def _ask_continue(self, steps: int) -> bool:
        callback = self._continue_turn
        if callback is None:
            return False

        loop = asyncio.get_running_loop()
        try:
            return bool(await loop.run_in_executor(None, callback, steps))
        except Exception:
            logger.exception("Continue callback failed.")
            return False

    async def _ask_confirmation(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[bool, str | None]:
        assert self._confirm_tool is not None

        loop = asyncio.get_running_loop()
        try:
            approved = await loop.run_in_executor(
                None, self._confirm_tool, tool_name, dict(arguments)
            )
        except Exception as exc:
            logger.exception("Tool confirmation callback failed.")
            return False, f"Tool confirmation failed: {exc}"
        return bool(approved), None

    def _extract_content(
        self, response: types.GenerateContentResponse
    ) -> types.Content:
        candidates = response.candidates or []
        if not candidates:
            feedback = getattr(response, "prompt_feedback", None)
            reason = getattr(feedback, "block_reason", None)
            suffix = f" (reason: {reason})" if reason else ""
            raise AgentError(
                "The model returned no candidates. The prompt may have been blocked"
                f"{suffix}."
            )

        candidate = candidates[0]
        content = candidate.content
        if content is None:
            finish_reason = getattr(candidate, "finish_reason", None)
            raise AgentError(
                "The model returned an empty response"
                + (f" (finish reason: {finish_reason})." if finish_reason else ".")
            )
        return content


def _parts_from_content(
    content: types.Content,
) -> tuple[list[types.FunctionCall], list[str]]:
    calls: list[types.FunctionCall] = []
    text_parts: list[str] = []
    for part in content.parts or []:
        if part.function_call is not None:
            calls.append(part.function_call)
        elif part.text and not part.thought:
            text_parts.append(part.text)
    return calls, text_parts


def _normalise_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Gemini normally returns a dict, but malformed calls must not crash us."""

    if raw is None:
        return {}, None
    if isinstance(raw, Mapping):
        try:
            return dict(raw), None
        except Exception as exc:  # noqa: BLE001
            return {}, f"Could not read tool arguments: {exc}"
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            return {}, f"Tool arguments were not valid JSON: {exc}"
        if isinstance(decoded, dict):
            return decoded, None
        return {}, "Tool arguments must be a JSON object."
    return {}, f"Tool arguments must be a JSON object, got {type(raw).__name__}."


def validate_tool_arguments(
    schema: Mapping[str, Any], arguments: Mapping[str, Any]
) -> str | None:
    """Validate arguments against the MCP tool's JSON Schema.

    Returns a human-readable error, or None when the arguments are valid.
    `jsonschema` uses the draft declared by the schema; MCP servers normally
    emit draft 2020-12 or draft 7.
    """

    if not schema:
        return None

    try:
        validator_class = jsonschema.validators.validator_for(schema)
        validator_class.check_schema(schema)
        validator = validator_class(schema)
        errors = sorted(
            validator.iter_errors(arguments),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except jsonschema.exceptions.SchemaError as exc:
        return f"The tool's input schema is invalid: {exc.message}"
    except Exception as exc:  # noqa: BLE001 - never crash on a bad schema
        return f"Could not validate tool arguments: {exc}"

    if not errors:
        return None

    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path) or "(root)"
    return f"Invalid arguments at {path}: {first.message}"


def _function_response(
    name: str,
    call_id: str | None,
    *,
    result: str | None = None,
    error: str | None = None,
) -> types.Part:
    payload: dict[str, Any] = (
        {"error": error} if error is not None else {"result": result}
    )
    response = types.FunctionResponse(name=name, response=payload)
    if call_id:
        response.id = call_id
    return types.Part(function_response=response)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    removed = len(text) - limit
    return f"{text[:limit]}\n... [truncated {removed} characters]"


def _safe_emitter(on_event: EventSink | None) -> Callable[[AgentEvent], None]:
    if on_event is None:
        return lambda event: None

    def emit(event: AgentEvent) -> None:
        try:
            on_event(event)
        except Exception:
            logger.exception("Event sink raised an exception.")

    return emit
