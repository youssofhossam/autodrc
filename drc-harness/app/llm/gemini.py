"""Thin Google Gemini adapter.

The rest of the application sees a small interface:

    build_tools(mcp_tools) -> GeminiToolSet
    generate(contents, tool_set) -> GenerateContentResponse

Conversation history is owned by the agent controller, not hidden inside a
chat object, so the agentic loop remains explicit and easy to follow.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from google import genai
from google.genai import errors, types

from app.llm.schema import GeminiToolSet, build_tool_set
from app.mcp_client.client import MCPTool

logger = logging.getLogger(__name__)

_API_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


class GeminiError(RuntimeError):
    """A Gemini request failed. Messages never contain the API key."""


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        system_instruction: str | None = None,
        temperature: float | None = None,
    ) -> None:
        if not api_key:
            raise GeminiError("GEMINI_API_KEY is not set.")
        if not model:
            raise GeminiError("GEMINI_MODEL is not set.")
        self._api_key = api_key
        self._model = model
        self._system_instruction = system_instruction
        self._temperature = temperature
        self._client = genai.Client(api_key=api_key)

    @property
    def model(self) -> str:
        return self._model

    def set_model(self, model: str) -> None:
        """Use another function-calling model for subsequent requests."""

        name = model.strip()
        if not name:
            raise GeminiError("GEMINI_MODEL is not set.")
        self._model = name

    def build_tools(self, mcp_tools: Sequence[MCPTool]) -> GeminiToolSet:
        """Convert MCP tools into Gemini function declarations."""

        return build_tool_set(mcp_tools)

    async def generate(
        self,
        contents: Sequence[types.Content],
        tool_set: GeminiToolSet,
    ) -> types.GenerateContentResponse:
        """Send the complete conversation to Gemini and return its response."""

        config_args: dict[str, object] = {
            "system_instruction": self._system_instruction,
            "temperature": self._temperature,
            # We execute tools ourselves through MCP. Automatic function
            # calling is disabled so the SDK cannot run anything implicitly.
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        }
        gemini_tools = tool_set.as_gemini_tools()
        if gemini_tools:
            config_args["tools"] = gemini_tools
            config_args["tool_config"] = types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO
                )
            )
        config = types.GenerateContentConfig(**config_args)

        try:
            return await self._client.aio.models.generate_content(
                model=self._model,
                contents=list(contents),
                config=config,
            )
        except errors.APIError as exc:
            code = getattr(exc, "code", None) or getattr(exc, "status_code", "?")
            detail = self._redact(getattr(exc, "message", None) or str(exc))
            if len(detail) > 4_000:
                detail = f"{detail[:4_000]}... [truncated]"
            hint = ""
            if code in (401, 403):
                hint = " Check GEMINI_API_KEY."
            elif code == 400:
                hint = (
                    " Check GEMINI_MODEL and the request; if the message "
                    "mentions an API key, check GEMINI_API_KEY."
                )
            raise GeminiError(f"Gemini API error ({code}):{hint} {detail}") from None
        except Exception as exc:  # noqa: BLE001 - network/transport errors
            detail = self._redact(str(exc)) or type(exc).__name__
            # from None: do not chain the original exception, which could
            # contain request details or credentials in its message.
            raise GeminiError(
                f"Gemini request failed ({type(exc).__name__}): {detail}"
            ) from None

    def _redact(self, text: str) -> str:
        text = text.replace(self._api_key, "[redacted]")
        return _API_KEY_PATTERN.sub("[redacted]", text)

    async def aclose(self) -> None:
        await self._client.aio.aclose()
