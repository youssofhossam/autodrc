"""Presentation-side data models.

These objects describe what the user sees. They are intentionally independent
of Gemini and MCP types; the agent history is stored as an opaque snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

MessageKind = Literal["user", "assistant", "system", "error"]
ToolStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class MessageItem:
    kind: MessageKind
    text: str
    title: str = ""


@dataclass
class ToolItem:
    name: str
    server: str
    arguments: dict[str, Any]
    status: ToolStatus = "running"
    result: str = ""
    is_error: bool = False
    summary: str = ""


TranscriptItem = MessageItem | ToolItem


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid4().hex)
    title: str = "New chat"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    items: list[TranscriptItem] = field(default_factory=list)
    # Opaque agent-owned conversation history.
    snapshot: Any | None = None


@dataclass(frozen=True)
class AppInfo:
    """Read-only configuration shown in the settings dialog."""

    model: str
    api_key_configured: bool
    mcp_servers: tuple[str, ...]
    mcp_config_path: str
    max_iterations: int
    max_result_chars: int
    temperature: float | None
    log_file: str


@dataclass(frozen=True)
class SettingsResult:
    model: str
    confirm_tool_calls: bool
    auto_expand_tools: bool


def derive_title(text: str, limit: int = 42) -> str:
    clean = " ".join(text.split())
    if not clean:
        return "New chat"
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"
