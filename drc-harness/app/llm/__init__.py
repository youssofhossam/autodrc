"""LLM layer: provider clients and MCP tool-schema conversion."""

from app.llm.factory import create_llm
from app.llm.gemini import GeminiClient, GeminiError
from app.llm.openrouter import OpenRouterClient, OpenRouterError
from app.llm.schema import (
    GeminiToolSet,
    ToolSet,
    ToolSpec,
    build_specs,
    build_tool_set,
    json_schema_to_gemini,
    sanitize_function_name,
)

__all__ = [
    "GeminiClient",
    "GeminiError",
    "GeminiToolSet",
    "OpenRouterClient",
    "OpenRouterError",
    "ToolSet",
    "ToolSpec",
    "build_specs",
    "build_tool_set",
    "create_llm",
    "json_schema_to_gemini",
    "sanitize_function_name",
]
