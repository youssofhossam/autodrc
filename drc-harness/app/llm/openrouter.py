"""OpenRouter adapter built on the OpenAI-compatible chat completions API.

OpenRouter exposes many models behind one endpoint and accepts MCP JSON
Schema directly, so this module does not use the Gemini schema converter.

The rest of the application still treats conversation history as Gemini
`types.Content` objects. That keeps the agent loop provider-neutral at its
boundary while this adapter translates to and from OpenAI messages.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from google.genai import types
from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    OpenAIError,
)

from app.llm.schema import ToolSet, build_specs
from app.mcp_client.client import MCPTool

logger = logging.getLogger(__name__)

_API_KEY_PATTERN = re.compile(r"sk-(?:or-v1-)?[0-9A-Za-z_\-]{16,}")
_FINISH_REASONS = {
    "stop": types.FinishReason.STOP,
    "length": types.FinishReason.MAX_TOKENS,
    "tool_calls": types.FinishReason.STOP,
    "content_filter": types.FinishReason.SAFETY,
}


class OpenRouterError(RuntimeError):
    """An OpenRouter request failed. Messages never contain the API key."""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        system_instruction: str | None = None,
        temperature: float | None = None,
        site_url: str = "",
        app_name: str = "",
    ) -> None:
        if not api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not set.")
        if not model:
            raise OpenRouterError("OPENROUTER_MODEL is not set.")

        self._api_key = api_key
        self._model = model
        self._system_instruction = system_instruction
        self._temperature = temperature

        headers: dict[str, str] = {}
        if site_url:
            headers["HTTP-Referer"] = site_url
        if app_name:
            headers["X-Title"] = app_name

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=headers or None,
        )

    @property
    def model(self) -> str:
        return self._model

    def set_model(self, model: str) -> None:
        """Use another OpenRouter model for subsequent requests."""

        name = model.strip()
        if not name:
            raise OpenRouterError("OPENROUTER_MODEL is not set.")
        self._model = name

    def build_tools(self, mcp_tools: Sequence[MCPTool]) -> ToolSet:
        """Expose MCP tools with the original JSON Schema."""

        specs, by_name = build_specs(mcp_tools)
        return ToolSet(
            mcp_tools=tuple(mcp_tools),
            specs=specs,
            by_function_name=MappingProxyType(by_name),
        )

    async def generate(
        self,
        contents: Sequence[types.Content],
        tool_set: ToolSet,
        system_instruction: str | None = None,
    ) -> types.GenerateContentResponse:
        """Send the complete conversation to OpenRouter and return its response.

        ``system_instruction`` overrides the client's default for this request,
        which lets the controller re-read ``AGENTS.md`` on every turn.
        """

        instruction = (
            self._system_instruction
            if system_instruction is None
            else system_instruction
        )
        request: dict[str, Any] = {
            "model": self._model,
            "messages": _to_openai_messages(instruction, contents),
        }
        if tool_set.specs:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": _tool_parameters(spec.parameters),
                    },
                }
                for spec in tool_set.specs
            ]
            request["tool_choice"] = "auto"
        if self._temperature is not None:
            request["temperature"] = self._temperature

        try:
            completion = await self._client.chat.completions.create(**request)
        except APIStatusError as exc:
            code = getattr(exc, "status_code", "?")
            detail = self._redact(getattr(exc, "message", None) or str(exc))
            if len(detail) > 4_000:
                detail = f"{detail[:4_000]}... [truncated]"
            raise OpenRouterError(
                f"OpenRouter API error ({code}):{_status_hint(code)} {detail}"
            ) from None
        except APIConnectionError as exc:
            detail = self._redact(str(exc)) or type(exc).__name__
            raise OpenRouterError(
                f"Could not reach OpenRouter ({type(exc).__name__}): {detail}"
            ) from None
        except OpenAIError as exc:
            detail = self._redact(str(exc)) or type(exc).__name__
            raise OpenRouterError(
                f"OpenRouter request failed ({type(exc).__name__}): {detail}"
            ) from None
        except Exception as exc:  # noqa: BLE001 - network/transport errors
            detail = self._redact(str(exc)) or type(exc).__name__
            raise OpenRouterError(
                f"OpenRouter request failed ({type(exc).__name__}): {detail}"
            ) from None

        return _to_gemini_response(completion)

    def _redact(self, text: str) -> str:
        text = text.replace(self._api_key, "[redacted]")
        return _API_KEY_PATTERN.sub("[redacted]", text)

    async def aclose(self) -> None:
        await self._client.close()


def _status_hint(code: Any) -> str:
    if code in (401, 403):
        return " Check OPENROUTER_API_KEY."
    if code == 402:
        return " Add credits at https://openrouter.ai/settings/credits."
    if code == 404:
        return (
            " Check OPENROUTER_MODEL; some models have no endpoint that "
            "supports tool calling."
        )
    if code == 429:
        return " Rate limit reached; wait and retry, or use a paid model."
    if code == 400:
        return (
            " Check OPENROUTER_MODEL and the request; the model may not "
            "support tool calling."
        )
    return ""


def _tool_parameters(schema: Mapping[str, Any]) -> dict[str, Any]:
    if not schema:
        return {"type": "object", "properties": {}}
    return dict(schema)


def _to_openai_messages(
    system_instruction: str | None,
    contents: Sequence[types.Content],
) -> list[dict[str, Any]]:
    """Translate the agent's Gemini-shaped history into OpenAI messages."""

    messages: list[dict[str, Any]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})

    # Tool calls from the most recent assistant turn that still need a result.
    pending_calls: list[tuple[str, str]] = []
    synthetic_counter = 0

    for content in contents:
        parts = list(content.parts or [])
        if content.role == "model":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            pending_calls = []
            for part in parts:
                call = part.function_call
                if call is not None:
                    synthetic_counter += 1
                    call_id = call.id or f"call_{synthetic_counter}"
                    name = call.name or ""
                    pending_calls.append((name, call_id))
                    tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": _arguments_json(call.args),
                            },
                        }
                    )
                elif part.text and not part.thought:
                    text_parts.append(part.text)

            message: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
            }
            if tool_calls:
                message["tool_calls"] = tool_calls
            if message["content"] or tool_calls:
                messages.append(message)
            continue

        # User-role turns carry plain text and/or function responses.
        text_buffer: list[str] = []
        for part in parts:
            response = part.function_response
            if response is not None:
                if text_buffer:
                    messages.append(
                        {"role": "user", "content": "\n".join(text_buffer)}
                    )
                    text_buffer.clear()
                call_id = _take_pending_call(response.id, pending_calls)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _tool_response_text(response.response),
                    }
                )
            elif part.text and not part.thought:
                text_buffer.append(part.text)
        if text_buffer:
            messages.append({"role": "user", "content": "\n".join(text_buffer)})

    return messages


def _take_pending_call(call_id: str | None, pending: list[tuple[str, str]]) -> str:
    if call_id:
        for index, (_, pending_id) in enumerate(pending):
            if pending_id == call_id:
                pending.pop(index)
                return call_id
        return call_id
    if pending:
        return pending.pop(0)[1]
    return "call_unknown"


def _arguments_json(arguments: Any) -> str:
    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        try:
            json.loads(arguments)
        except json.JSONDecodeError:
            return "{}"
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


def _tool_response_text(payload: Any) -> str:
    if isinstance(payload, Mapping):
        if "error" in payload:
            return f"Tool error: {payload['error']}"
        if "result" in payload:
            return str(payload["result"])
        try:
            return json.dumps(dict(payload), ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(payload)
    return str(payload)


def _to_gemini_response(completion: Any) -> types.GenerateContentResponse:
    """Wrap an OpenAI completion in the response shape the controller expects."""

    choices = getattr(completion, "choices", None) or []
    if not choices:
        raise OpenRouterError("OpenRouter returned no choices.")

    choice = choices[0]
    message = choice.message
    parts: list[types.Part] = []

    text = _message_text(getattr(message, "content", None))
    if text:
        parts.append(types.Part(text=text))

    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        if function is None:
            continue
        arguments = _parse_arguments(getattr(function, "arguments", None))
        parts.append(
            types.Part(
                function_call=_make_function_call(
                    getattr(function, "name", None) or "",
                    arguments,
                    getattr(call, "id", None),
                )
            )
        )

    finish_name = getattr(choice, "finish_reason", None)
    if not parts:
        suffix = f" (finish reason: {finish_name})" if finish_name else ""
        raise OpenRouterError(f"OpenRouter returned an empty response{suffix}.")

    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=_FINISH_REASONS.get(
                    str(finish_name or "").lower(), types.FinishReason.OTHER
                ),
            )
        ]
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        return "\n".join(piece for piece in pieces if piece)
    return ""


def _parse_arguments(raw: Any) -> Any:
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Keep the malformed string so the controller can report it back
            # to the model through its normal argument-error path.
            return raw
    return raw


def _make_function_call(
    name: str, arguments: Any, call_id: str | None
) -> types.FunctionCall:
    if isinstance(arguments, Mapping):
        return types.FunctionCall(name=name, args=dict(arguments), id=call_id)
    # Pydantic's FunctionCall expects a mapping. Malformed arguments are kept
    # as a string so the controller can return a useful error to the model.
    return types.FunctionCall.model_construct(name=name, args=arguments, id=call_id)
