from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.agent.events import (
    AgentEvent,
    AssistantEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.gui.models import AppInfo
from app.gui.theme import apply_theme
from app.gui.window import ChatWindow


class FakeAgent:
    model = "gemini-test"
    tool_count = 2

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.history: list[str] = []
        self.calls: list[str] = []
        self.started = 0
        self.shutdowns = 0
        self.confirmation_callback: Callable[[str, dict[str, Any]], bool] | None = None

    async def start(self) -> None:
        await asyncio.sleep(0.01)
        self.started += 1

    async def send_message(
        self,
        text: str,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        self.calls.append(text)
        emit = on_event or (lambda _event: None)
        emit(StatusEvent("Thinking (step 1/2)..."))
        await asyncio.sleep(0.02)
        if self.fail:
            raise RuntimeError("simulated Gemini failure")
        emit(AssistantEvent("Let me check the files."))
        emit(
            ToolCallEvent(
                "filesystem.list_directory",
                "filesystem",
                {"path": "."},
            )
        )
        await asyncio.sleep(0.02)
        emit(
            ToolResultEvent(
                "filesystem.list_directory", "a.txt\nb.txt\nc.txt", False
            )
        )
        emit(StatusEvent("Thinking (step 2/2)..."))
        await asyncio.sleep(0.02)
        emit(AssistantEvent("There are three files."))
        answer = "There are three files."
        self.history = [text, answer]
        return answer

    async def shutdown(self) -> None:
        self.shutdowns += 1

    def clear(self) -> None:
        self.history.clear()

    def set_confirmation_callback(
        self, callback: Callable[[str, dict[str, Any]], bool] | None
    ) -> None:
        self.confirmation_callback = callback

    def snapshot_history(self) -> tuple[str, ...]:
        return tuple(self.history)

    def restore_history(self, contents: Any | None) -> None:
        self.history = list(contents or ())

    def set_model(self, model: str) -> None:
        self.model = model


def _info() -> AppInfo:
    return AppInfo(
        model="gemini-test",
        api_key_configured=True,
        mcp_servers=("filesystem",),
        mcp_config_path="/tmp/servers.json",
        max_iterations=8,
        max_result_chars=20_000,
        temperature=None,
        log_file="/tmp/autodrc-test/app.log",
    )


@pytest.fixture(scope="session")
def qapp(tmp_path_factory: pytest.TempPathFactory) -> QApplication:
    settings_dir = tmp_path_factory.mktemp("qt-settings")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir)
    )
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    yield app


@pytest.fixture
def window(qapp: QApplication):
    QSettings("autodrc", "autodrc").clear()
    agent = FakeAgent()
    win = ChatWindow(agent, _info(), confirm_tool_calls=False)
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    try:
        yield win, agent
    finally:
        if not win._closing:
            win.close()
        qapp.processEvents()


def _wait_for(
    app: QApplication,
    predicate: Callable[[], bool],
    timeout: float = 8.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        QTest.qWait(15)
    return predicate()


def test_window_connects_and_runs_a_turn_with_tool_activity(
    qapp: QApplication, window
) -> None:
    win, agent = window
    assert _wait_for(qapp, lambda: agent.started >= 1)
    assert _wait_for(qapp, lambda: win._mcp_connected is True)

    win.composer.set_text("How many files are here?")
    win._send_message(win.composer.text())
    assert win._busy is True
    assert win.composer.send_button.isEnabled() is False
    assert win._sidebar.list.isEnabled() is False

    assert _wait_for(qapp, lambda: not win._busy)
    assert agent.calls == ["How many files are here?"]
    assert win._status_pill.text.text() == "Ready"
    assert win.composer.send_button.isEnabled() is False  # input is empty
    assert win._sidebar.list.isEnabled() is True

    conversation = win._active_conversation()
    assert conversation is not None
    assert conversation.title == "How many files are here?"
    kinds = [
        (type(item).__name__, getattr(item, "kind", getattr(item, "status", "")))
        for item in conversation.items
    ]
    assert kinds == [
        ("MessageItem", "user"),
        ("MessageItem", "assistant"),
        ("ToolItem", "completed"),
        ("MessageItem", "assistant"),
    ]
    assert conversation.snapshot == (
        "How many files are here?",
        "There are three files.",
    )
    tool_item = conversation.items[2]
    assert tool_item.result.startswith("a.txt")
    assert tool_item.server == "filesystem"


def test_new_chat_and_restore_preserve_transcript(
    qapp: QApplication, window
) -> None:
    win, agent = window
    assert _wait_for(qapp, lambda: agent.started >= 1)
    win._send_message("First question")
    assert _wait_for(qapp, lambda: not win._busy)
    first = win._active_conversation()
    assert first is not None
    first_items = list(first.items)

    win._new_chat()
    assert len(win._active_conversation().items) == 0
    assert win._chat.currentIndex() == 0
    assert win._agent.history == []

    win._select_conversation(first.id)
    assert win._active_conversation() is first
    assert len(first.items) == len(first_items)
    assert win._chat.currentIndex() == 1


def test_turn_failure_is_shown_inline_without_crashing(
    qapp: QApplication, window
) -> None:
    win, agent = window
    agent.fail = True
    assert _wait_for(qapp, lambda: agent.started >= 1)

    win._send_message("Please fail")
    assert _wait_for(qapp, lambda: not win._busy)

    conversation = win._active_conversation()
    assert conversation is not None
    assert conversation.items[-1].kind == "error"
    assert "simulated Gemini failure" in conversation.items[-1].text
    assert win._status_pill.text.text() == "Error"


def test_confirmation_is_auto_approved_when_disabled(window) -> None:
    win, agent = window
    assert agent.confirmation_callback is not None
    assert win._confirm_enabled is False
    assert agent.confirmation_callback("some_tool", {"x": 1}) is True


def test_header_and_tool_summary_are_useful(qapp: QApplication, window) -> None:
    win, agent = window
    assert _wait_for(qapp, lambda: agent.started >= 1)
    win._send_message("Check the directory")
    assert _wait_for(qapp, lambda: not win._busy)

    conversation = win._active_conversation()
    assert conversation is not None
    assert conversation.title == "Check the directory"
    assert "gemini-test" in win._subtitle_label.text()
