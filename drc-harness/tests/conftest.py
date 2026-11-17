"""Shared test doubles. No MCP server is created here."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from google.genai import types

from app.llm.schema import GeminiToolSet, build_tool_set
from app.mcp_client.client import MCPTool, ToolResult


def make_tool(
    name: str = "echo",
    *,
    server: str = "test-server",
    schema: dict[str, Any] | None = None,
    description: str = "Echo some text.",
) -> MCPTool:
    if schema is None:
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
    return MCPTool(
        server=server,
        name=name,
        description=description,
        input_schema=schema,
    )


def text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)])
            )
        ]
    )


def function_call(
    name: str, args: Any = None, call_id: str | None = None
) -> types.FunctionCall:
    # The real model normally sends a dict. The string case lets tests cover
    # malformed arguments without pydantic rejecting them while constructing
    # the fake response.
    if isinstance(args, str):
        return types.FunctionCall.model_construct(name=name, args=args, id=call_id)
    return types.FunctionCall(name=name, args=args, id=call_id)


def tool_call_response(
    *calls: types.FunctionCall, text: str | None = None
) -> types.GenerateContentResponse:
    parts: list[types.Part] = []
    if text:
        parts.append(types.Part(text=text))
    parts.extend(types.Part(function_call=call) for call in calls)
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


class ScriptedLLM:
    """Returns pre-scripted Gemini responses and records every request."""

    def __init__(self, responses: Sequence[types.GenerateContentResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[list[types.Content]] = []
        self.tool_sets: list[GeminiToolSet] = []
        self.system_instructions: list[str | None] = []
        self.closed = False
        self.model = "scripted-model"

    def set_model(self, model: str) -> None:
        self.model = model

    def build_tools(self, mcp_tools: Sequence[MCPTool]) -> GeminiToolSet:
        return build_tool_set(mcp_tools)

    async def generate(
        self,
        contents: Sequence[types.Content],
        tool_set: GeminiToolSet,
        system_instruction: str | None = None,
    ) -> types.GenerateContentResponse:
        self.requests.append([content.model_copy(deep=True) for content in contents])
        self.tool_sets.append(tool_set)
        self.system_instructions.append(system_instruction)
        if not self._responses:
            raise AssertionError("ScriptedLLM ran out of responses.")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


class FakeMCP:
    """An in-memory stand-in for MCPClient; it never launches a process."""

    def __init__(
        self,
        tools: Sequence[MCPTool] | None = None,
        *,
        results: dict[str, ToolResult] | None = None,
        failures: dict[str, BaseException] | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self.tools = list(tools if tools is not None else [make_tool()])
        self.results = results or {}
        self.failures = failures or {}
        self.connect_error = connect_error
        self.calls: list[tuple[MCPTool, dict[str, Any]]] = []
        self.connect_count = 0
        self.close_count = 0
        self.list_count = 0

    async def connect(self) -> None:
        self.connect_count += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def list_tools(self) -> list[MCPTool]:
        self.list_count += 1
        return list(self.tools)

    async def call_tool(self, tool: MCPTool, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((tool, dict(arguments)))
        if tool.name in self.failures:
            raise self.failures[tool.name]
        return self.results.get(
            tool.name, ToolResult(f"{tool.name} result for {arguments}")
        )

    async def close(self) -> None:
        self.close_count += 1


@pytest.fixture
def echo_tool() -> MCPTool:
    return make_tool()
