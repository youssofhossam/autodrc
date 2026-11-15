"""Events emitted by the agent while it works.

The GUI consumes these events to show tool activity. The agent does not import
Qt and does not know how events are displayed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StatusEvent:
    """A short progress message, e.g. "Thinking..."."""

    text: str


@dataclass(frozen=True)
class AssistantEvent:
    """Text produced by Gemini.

    This may be a final answer or an intermediate message that accompanies
    function calls.
    """

    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    """The agent is about to execute an MCP tool."""

    name: str
    server: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResultEvent:
    """An MCP tool returned (or failed)."""

    name: str
    text: str
    is_error: bool = False


AgentEvent = StatusEvent | AssistantEvent | ToolCallEvent | ToolResultEvent
