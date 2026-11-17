from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from app.agent.events import (
    AgentEvent,
    AssistantEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.gui.bridge import AgentBridge, ContinueRequest
from app.gui.dialogs import SettingsDialog
from app.gui.models import AppInfo, ProviderInfo, SettingsResult
from app.gui.theme import apply_theme
from app.gui.window import ChatWindow


class FakeAgent:
    model = "gemini-test"
    tool_count = 2

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.provider = "gemini"
        self.history: list[str] = []
        self.calls: list[str] = []
        self.started = 0
        self.shutdowns = 0
        self.confirmation_callback: Callable[[str, dict[str, Any]], bool] | None = None
        self.continue_callback: Callable[[int], bool] | None = None

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

    def set_continue_callback(
        self, callback: Callable[[int], bool] | None
    ) -> None:
        self.continue_callback = callback

    def snapshot_history(self) -> tuple[str, ...]:
        return tuple(self.history)

    def restore_history(self, contents: Any | None) -> None:
        self.history = list(contents or ())

    def set_model(self, model: str) -> None:
        self.model = model

    def set_provider(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model


def _info() -> AppInfo:
    return AppInfo(
        provider="gemini",
        model="gemini-test",
        api_key_configured=True,
        mcp_servers=("filesystem",),
        mcp_config_path="/tmp/servers.json",
        max_iterations=1000,
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


class SlowAgent(FakeAgent):
    """A turn that keeps running until it is cancelled."""

    async def send_message(
        self,
        text: str,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> str:
        self.calls.append(text)
        emit = on_event or (lambda _event: None)
        emit(StatusEvent("Thinking (step 1/2)..."))
        await asyncio.sleep(30)
        return "never reached"


def _make_window(qapp: QApplication, agent: FakeAgent) -> ChatWindow:
    QSettings("autodrc", "autodrc").clear()
    win = ChatWindow(agent, _info(), confirm_tool_calls=False)
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    return win


def test_stop_button_replaces_send_while_the_turn_runs(qapp: QApplication) -> None:
    win = _make_window(qapp, SlowAgent())
    try:
        win.composer.set_text("Will be sent")
        assert win.composer.send_button.isVisibleTo(win) is True
        assert win.composer.stop_button.isVisibleTo(win) is False

        win._send_message("Please think for a while")
        qapp.processEvents()

        assert win._busy is True
        assert win.composer.stop_button.isVisibleTo(win) is True
        assert win.composer.stop_button.isEnabled() is True
        assert win.composer.send_button.isVisibleTo(win) is False

        win._stop_turn()
        assert _wait_for(qapp, lambda: not win._busy)
    finally:
        win.close()
        qapp.processEvents()


def test_stop_button_interrupts_the_turn_and_frees_the_composer(
    qapp: QApplication,
) -> None:
    win = _make_window(qapp, SlowAgent())
    try:
        assert _wait_for(qapp, lambda: win._mcp_connected is True)
        win._send_message("Please think for a while")
        qapp.processEvents()
        assert win._busy is True

        win.composer.stop_button.click()

        assert _wait_for(qapp, lambda: not win._busy)
        assert win._status_pill.text.text() == "Stopped"
        assert win.composer.stop_button.isVisibleTo(win) is False
        assert win.composer.send_button.isVisibleTo(win) is True
        assert win._sidebar.list.isEnabled() is True

        conversation = win._active_conversation()
        assert conversation is not None
        assert conversation.items[-1].kind == "system"
        assert "Stopped" in conversation.items[-1].text

        # The agent is idle again: a new message is accepted immediately.
        win.composer.set_text("Second question")
        assert win.composer.send_button.isEnabled() is True
    finally:
        win.close()
        qapp.processEvents()


def test_stop_is_ignored_when_no_turn_is_running(window) -> None:
    win, _agent = window
    win._stop_turn()

    assert win._busy is False
    assert win._status_pill.text.text() in {"Ready", "Connecting…", "MCP unavailable"}
    assert win.composer.stop_button.isVisibleTo(win) is False


def test_confirmation_is_auto_approved_when_disabled(window) -> None:
    win, agent = window
    assert agent.confirmation_callback is not None
    assert win._confirm_enabled is False
    assert agent.confirmation_callback("some_tool", {"x": 1}) is True


def test_continue_prompt_is_shown_and_resolved(qapp: QApplication, window, monkeypatch) -> None:
    win, _agent = window

    class AcceptingDialog:
        approved = True

        def __init__(self, steps: int, *, parent=None) -> None:
            self.steps = steps

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("app.gui.window.ContinueDialog", AcceptingDialog)
    request = ContinueRequest(1000)

    win._on_continue_requested(request)

    assert request.approved is True
    assert win._status_pill.text.toolTip() == "Continuing…"


def test_continue_request_is_declined_while_closing(window) -> None:
    win, _agent = window
    win._closing = True
    request = ContinueRequest(1000)

    win._on_continue_requested(request)

    assert request.approved is False


def test_bridge_routes_continue_requests_to_the_gui(qapp: QApplication) -> None:
    agent = FakeAgent()
    bridge = AgentBridge(agent)
    try:
        assert agent.continue_callback is not None
        bridge.continue_requested.connect(lambda request: request.resolve(True))
        assert agent.continue_callback(1000) is True

        bridge.continue_requested.disconnect()
        bridge.continue_requested.connect(lambda request: request.resolve(False))
        assert agent.continue_callback(1000) is False
    finally:
        bridge.shutdown()


def test_header_and_tool_summary_are_useful(qapp: QApplication, window) -> None:
    win, agent = window
    assert _wait_for(qapp, lambda: agent.started >= 1)
    win._send_message("Check the directory")
    assert _wait_for(qapp, lambda: not win._busy)

    conversation = win._active_conversation()
    assert conversation is not None
    assert conversation.title == "Check the directory"
    assert "gemini-test" in win._subtitle_label.text()


def test_settings_dialog_uses_provider_model_choices(qapp: QApplication) -> None:
    info = replace(
        _info(),
        provider="openrouter",
        model="google/gemini-2.5-flash",
    )
    dialog = SettingsDialog(
        info,
        model=info.model,
        mcp_connected=True,
        mcp_error="",
        tool_count=0,
        confirm_tool_calls=True,
        auto_expand_tools=False,
        busy=False,
    )
    items = [
        dialog.model_combo.itemText(index)
        for index in range(dialog.model_combo.count())
    ]

    assert info.provider_name == "OpenRouter"
    assert items[0] == "google/gemini-2.5-flash"
    assert "anthropic/claude-sonnet-4" in items
    assert "gemini-2.5-pro" not in items
    assert dialog.provider_combo.currentData() == "openrouter"
    dialog.deleteLater()


def test_settings_provider_selector_switches_the_model_choices(
    qapp: QApplication,
) -> None:
    info = replace(
        _info(),
        providers=(
            ProviderInfo("gemini", "gemini-test", True),
            ProviderInfo("openrouter", "openrouter-test", True),
            ProviderInfo("other", "other-model", False),
        ),
    )
    dialog = SettingsDialog(
        info,
        model=info.model,
        mcp_connected=True,
        mcp_error="",
        tool_count=0,
        confirm_tool_calls=True,
        auto_expand_tools=False,
        busy=False,
    )

    # Providers without an API key are not selectable.
    assert dialog.provider_combo.findData("other") == -1
    assert dialog.model_combo.currentText() == "gemini-test"

    dialog.provider_combo.setCurrentIndex(
        dialog.provider_combo.findData("openrouter")
    )
    assert dialog.model_combo.currentText() == "openrouter-test"
    assert "anthropic/claude-sonnet-4" in [
        dialog.model_combo.itemText(index)
        for index in range(dialog.model_combo.count())
    ]

    dialog._accept()
    assert dialog.result_settings.provider == "openrouter"
    assert dialog.result_settings.model == "openrouter-test"
    dialog.deleteLater()


def test_switching_provider_updates_the_agent_and_header(
    qapp: QApplication, window, monkeypatch: pytest.MonkeyPatch
) -> None:
    win, agent = window
    assert _wait_for(qapp, lambda: agent.started >= 1)
    win._info = replace(
        win._info,
        providers=(
            ProviderInfo("gemini", "gemini-test", True),
            ProviderInfo("openrouter", "openrouter-test", True),
        ),
    )

    class AcceptingDialog:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.result_settings = SettingsResult(
                model="openrouter-test",
                confirm_tool_calls=win._confirm_enabled,
                auto_expand_tools=win._auto_expand,
                provider="openrouter",
            )

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr("app.gui.window.SettingsDialog", AcceptingDialog)
    win._open_settings()

    assert agent.provider == "openrouter"
    assert agent.model == "openrouter-test"
    assert win._provider == "openrouter"
    assert win._model == "openrouter-test"
    assert "OpenRouter" in win._subtitle_label.text()
