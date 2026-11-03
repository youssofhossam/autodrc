from __future__ import annotations

import asyncio
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any

import pytest

from app.agent.events import AgentEvent, AssistantEvent
from app.gui.chatbot import ChatbotApp


class SlowFakeAgent:
    def __init__(self, delay: float = 0.25) -> None:
        self.delay = delay
        self.started = 0
        self.shutdowns = 0
        self.cleared = 0
        self.confirmation_callback: Callable[[str, dict[str, Any]], bool] | None = None

    async def start(self) -> None:
        await asyncio.sleep(0.01)
        self.started += 1

    async def send_message(
        self,
        text: str,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        await asyncio.sleep(self.delay)
        answer = f"Echo: {text}"
        if on_event is not None:
            on_event(AssistantEvent(answer))
        return answer

    async def shutdown(self) -> None:
        self.shutdowns += 1

    def clear(self) -> None:
        self.cleared += 1

    def set_confirmation_callback(
        self, callback: Callable[[str, dict[str, Any]], bool] | None
    ) -> None:
        self.confirmation_callback = callback


def _require_display() -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
    except tk.TclError as exc:
        pytest.skip(f"Tkinter display unavailable: {exc}")


def _pump(app: ChatbotApp, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app._root.update()
        time.sleep(0.01)


def test_gui_stays_responsive_and_displays_final_answer() -> None:
    _require_display()
    agent = SlowFakeAgent(delay=0.3)
    app = ChatbotApp(agent, confirm_tool_calls=False)
    app._root.withdraw()

    try:
        _pump(app, 0.2)
        assert agent.started == 1
        assert app._status_var.get() == "Ready"

        app._input.insert("1.0", "hello")
        started = time.monotonic()
        app._on_send()
        assert time.monotonic() - started < 0.1
        assert app._busy is True

        # The main loop keeps processing events while the request sleeps.
        _pump(app, 0.08)
        assert app._busy is True
        assert app._status_var.get() == "Thinking..."

        _pump(app, 0.45)
        assert app._busy is False
        assert app._status_var.get() == "Ready"
        transcript = app._chat.get("1.0", "end")
        assert "[You]" in transcript
        assert "Echo: hello" in transcript
    finally:
        app._on_close()

    assert agent.shutdowns == 1


def test_gui_confirmation_can_be_skipped_with_checkbox() -> None:
    _require_display()
    agent = SlowFakeAgent()
    app = ChatbotApp(agent, confirm_tool_calls=False)
    app._root.withdraw()

    try:
        assert app._confirm_var.get() is False
        assert app._confirm_tool("some_tool", {"x": 1}) is True
    finally:
        app._on_close()
