"""The boundary between the GUI and the application/agent layer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from app.agent.events import AgentEvent


class ChatAgent(Protocol):
    """The small part of the agent the GUI depends on."""

    @property
    def model(self) -> str: ...

    @property
    def tool_count(self) -> int: ...

    async def start(self) -> None: ...

    async def send_message(
        self,
        text: str,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> str: ...

    async def shutdown(self) -> None: ...

    def clear(self) -> None: ...

    def set_confirmation_callback(
        self, callback: Callable[[str, dict[str, Any]], bool] | None
    ) -> None: ...

    def snapshot_history(self) -> Any: ...

    def restore_history(self, contents: Any | None) -> None: ...

    def set_model(self, model: str) -> None: ...

    def set_provider(self, provider: str, model: str) -> None: ...
