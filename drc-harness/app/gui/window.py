"""Main Qt window for autodrc."""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.agent.events import (
    AgentEvent,
    AssistantEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.gui.bridge import AgentBridge, ConfirmationRequest, ContinueRequest
from app.gui.chat_view import ChatView, ElidedLabel
from app.gui.composer import Composer
from app.gui.dialogs import (
    ConfirmToolDialog,
    ContinueDialog,
    SettingsDialog,
)
from app.gui.icons import app_icon, icon
from app.gui.models import (
    AppInfo,
    Conversation,
    MessageItem,
    ToolItem,
    derive_title,
    provider_display_name,
)
from app.gui.protocol import ChatAgent
from app.gui.sidebar import Sidebar
from app.gui.theme import COLORS

logger = logging.getLogger(__name__)


class StatusPill(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(24)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 2, 10, 2)
        layout.setSpacing(6)
        self.dot = QLabel()
        self.dot.setFixedSize(7, 7)
        self.text = QLabel("Ready")
        self.text.setObjectName("StatusPillText")
        self.text.setMaximumWidth(250)
        layout.addWidget(self.dot)
        layout.addWidget(self.text)
        self.set_status("Ready", "idle")

    def set_status(self, text: str, kind: str = "idle") -> None:
        colours = {
            "idle": COLORS["success"],
            "busy": COLORS["accent"],
            "error": COLORS["danger"],
            "warning": COLORS["warning"],
        }
        colour = colours.get(kind, COLORS["text_muted"])
        self.dot.setStyleSheet(
            f"background-color: {colour}; border-radius: 3px;"
        )
        self.text.setToolTip(text)
        self.text.setText(text if len(text) <= 42 else text[:41] + "…")
        self.text.setStyleSheet(
            f"color: {colour if kind != 'idle' else COLORS['text_muted']};"
        )


class StatusBanner(QFrame):
    retry_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 7, 12, 7)
        layout.setSpacing(9)

        icon_label = QLabel()
        icon_label.setPixmap(icon("alert", COLORS["warning"], 16).pixmap(16, 16))
        icon_label.setFixedSize(16, 16)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        self.label = QLabel()
        self.label.setObjectName("BannerText")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        retry = QPushButton("Retry")
        retry.setObjectName("BannerButton")
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.clicked.connect(self.retry_requested)
        layout.addWidget(retry)

        dismiss = QToolButton()
        dismiss.setObjectName("IconButton")
        dismiss.setIcon(icon("close", COLORS["warning"], 13))
        dismiss.setIconSize(QSize(13, 13))
        dismiss.setToolTip("Dismiss")
        dismiss.clicked.connect(self.dismissed)
        layout.addWidget(dismiss)
        self.hide()

    def show_message(self, text: str) -> None:
        self.label.setText(text)
        self.show()


class ChatWindow(QMainWindow):
    def __init__(
        self,
        agent: ChatAgent,
        app_info: AppInfo,
        *,
        confirm_tool_calls: bool = True,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._info = app_info
        self._provider = app_info.provider
        self._model = agent.model or app_info.model
        self._settings = _settings_store()
        self._conversations: list[Conversation] = []
        self._active_id: str | None = None
        self._current_assistant = None
        self._current_assistant_item: MessageItem | None = None
        self._turn_had_assistant = False
        self._busy = False
        self._closing = False
        self._mcp_connected: bool | None = None
        self._mcp_error = ""
        self._tool_count = 0
        self._sidebar_width = 270

        self._confirm_enabled = _load_bool(
            self._settings, "confirm_tool_calls", confirm_tool_calls
        )
        self._auto_expand = _load_bool(
            self._settings, "auto_expand_tools", False
        )

        self.setWindowTitle("autodrc")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(900, 600)
        self.resize(1180, 800)

        self._bridge = AgentBridge(agent, parent=self)
        self._bridge.set_confirmation_enabled(self._confirm_enabled)
        self._bridge.event.connect(self._on_agent_event)
        self._bridge.turn_finished.connect(self._on_turn_finished)
        self._bridge.turn_cancelled.connect(self._on_turn_cancelled)
        self._bridge.turn_failed.connect(self._on_turn_failed)
        self._bridge.connection_ready.connect(self._on_connection_ready)
        self._bridge.connection_failed.connect(self._on_connection_failed)
        self._bridge.confirmation_requested.connect(self._on_confirmation_requested)
        self._bridge.continue_requested.connect(self._on_continue_requested)

        self._build_ui()
        self._chat.set_auto_expand(self._auto_expand)
        self._restore_window_state()
        self._create_initial_conversation()
        self._setup_shortcuts()

        self._sidebar.set_connection(None)
        self._set_status("Connecting…", "warning")
        self._bridge.start_agent()
        QTimer.singleShot(0, self.composer.focus_input)

    # ------------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)
        root_layout.addWidget(self._splitter)

        self._sidebar = Sidebar()
        self._sidebar.new_chat_requested.connect(self._new_chat)
        self._sidebar.conversation_selected.connect(self._select_conversation)
        self._sidebar.rename_requested.connect(self._rename_conversation)
        self._sidebar.delete_requested.connect(self._delete_conversation)
        self._sidebar.settings_requested.connect(self._open_settings)
        self._sidebar.reconnect_requested.connect(self._retry_connection)
        self._splitter.addWidget(self._sidebar)

        main = QWidget()
        main.setObjectName("AppRoot")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self._splitter.addWidget(main)

        top = QFrame()
        top.setObjectName("TopBar")
        top.setFixedHeight(64)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(16, 0, 16, 0)
        top_layout.setSpacing(10)

        self._sidebar_button = QToolButton()
        self._sidebar_button.setObjectName("IconButton")
        self._sidebar_button.setIcon(icon("sidebar", COLORS["text_muted"], 18))
        self._sidebar_button.setIconSize(QSize(18, 18))
        self._sidebar_button.setToolTip("Hide sidebar (Ctrl+B)")
        self._sidebar_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sidebar_button.clicked.connect(self._toggle_sidebar)
        top_layout.addWidget(self._sidebar_button)

        title_block = QVBoxLayout()
        title_block.setContentsMargins(4, 0, 0, 0)
        title_block.setSpacing(1)
        self._title_label = ElidedLabel("New chat")
        self._title_label.setObjectName("ConversationTitle")
        self._title_label.setMinimumWidth(120)
        self._title_label.setMaximumWidth(420)
        self._subtitle_label = QLabel()
        self._subtitle_label.setObjectName("ConversationSubtitle")
        title_block.addWidget(self._title_label)
        title_block.addWidget(self._subtitle_label)
        top_layout.addLayout(title_block)

        top_layout.addStretch(1)
        self._status_pill = StatusPill()
        top_layout.addWidget(self._status_pill)

        self._settings_button = QToolButton()
        self._settings_button.setObjectName("IconButton")
        self._settings_button.setIcon(icon("settings", COLORS["text_muted"], 18))
        self._settings_button.setIconSize(QSize(18, 18))
        self._settings_button.setToolTip("Settings (Ctrl+,)")
        self._settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_button.clicked.connect(self._open_settings)
        top_layout.addWidget(self._settings_button)

        main_layout.addWidget(top)

        self._banner = StatusBanner()
        self._banner.retry_requested.connect(self._retry_connection)
        self._banner.dismissed.connect(self._banner.hide)
        main_layout.addWidget(self._banner)

        self._chat = ChatView()
        self._chat.suggestion_selected.connect(self._use_suggestion)
        main_layout.addWidget(self._chat, 1)

        composer_holder = QWidget()
        composer_layout = QHBoxLayout(composer_holder)
        composer_layout.setContentsMargins(28, 2, 28, 18)
        composer_layout.setSpacing(0)
        self.composer = Composer()
        self.composer.setMaximumWidth(1000)
        self.composer.submitted.connect(self._send_message)
        self.composer.stop_requested.connect(self._stop_turn)
        composer_layout.addStretch(1)
        composer_layout.addWidget(self.composer, 8)
        composer_layout.addStretch(1)
        main_layout.addWidget(composer_holder)

        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([270, 910])

    def _setup_shortcuts(self) -> None:
        for keys, handler in (
            ("Ctrl+N", self._new_chat),
            ("Ctrl+,", self._open_settings),
            ("Ctrl+B", self._toggle_sidebar),
            ("Ctrl+L", self.composer.focus_input),
            ("Esc", self._stop_turn),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(handler)

    # ------------------------------------------------------- conversations

    def _create_initial_conversation(self) -> None:
        conversation = Conversation()
        self._conversations.append(conversation)
        self._active_id = conversation.id
        self._sidebar.set_conversations(
            [(conversation.id, conversation.title)], conversation.id
        )
        self._chat.clear()
        self._update_header()

    def _active_conversation(self) -> Conversation | None:
        for conversation in self._conversations:
            if conversation.id == self._active_id:
                return conversation
        return None

    def _new_chat(self) -> None:
        if self._busy or self._closing:
            return
        active = self._active_conversation()
        if active is not None and not active.items:
            self.composer.focus_input()
            return

        self._save_snapshot()
        conversation = Conversation()
        self._conversations.insert(0, conversation)
        self._active_id = conversation.id
        self._current_assistant = None
        self._current_assistant_item = None
        try:
            self._agent.restore_history(None)
        except Exception as exc:  # noqa: BLE001 - surface agent failures in the UI
            self._show_error("Could not start a new conversation", str(exc))
            return
        self._chat.clear()
        self._sidebar.add_conversation(conversation.id, conversation.title)
        self._update_header()
        self._set_status(
            "Ready" if self._mcp_connected else "MCP unavailable",
            "idle" if self._mcp_connected else "warning",
        )
        self.composer.clear()
        self.composer.focus_input()

    def _select_conversation(self, conversation_id: str) -> None:
        if self._busy or self._closing:
            if self._active_id:
                self._sidebar.select(self._active_id)
            return
        if conversation_id == self._active_id:
            return
        conversation = next(
            (item for item in self._conversations if item.id == conversation_id),
            None,
        )
        if conversation is None:
            return

        self._save_snapshot()
        try:
            self._agent.restore_history(conversation.snapshot)
        except Exception as exc:  # noqa: BLE001 - surface agent failures in the UI
            self._sidebar.select(self._active_id or conversation_id)
            self._show_error("Could not switch conversations", str(exc))
            return

        self._active_id = conversation.id
        self._current_assistant = None
        self._current_assistant_item = None
        self._chat.load_items(conversation.items)
        self._sidebar.select(conversation.id)
        self._update_header()
        self._set_status(
            "Ready" if self._mcp_connected is not False else "MCP unavailable",
            "idle" if self._mcp_connected is not False else "warning",
        )
        self.composer.focus_input()

    def _save_snapshot(self) -> None:
        conversation = self._active_conversation()
        if conversation is None or self._busy:
            return
        try:
            conversation.snapshot = self._agent.snapshot_history()
        except Exception:
            logger.exception("Could not snapshot the active conversation.")

    def _rename_conversation(self, conversation_id: str) -> None:
        if self._busy:
            return
        conversation = next(
            (item for item in self._conversations if item.id == conversation_id),
            None,
        )
        if conversation is None:
            return
        title, accepted = QInputDialog.getText(
            self, "Rename conversation", "Title:", text=conversation.title
        )
        title = title.strip()
        if not accepted or not title:
            return
        conversation.title = title
        self._sidebar.update_title(conversation_id, title)
        if conversation.id == self._active_id:
            self._update_header()

    def _delete_conversation(self, conversation_id: str) -> None:
        if self._busy:
            return
        conversation = next(
            (item for item in self._conversations if item.id == conversation_id),
            None,
        )
        if conversation is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete conversation",
            f"Delete “{conversation.title}”? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        if len(self._conversations) == 1:
            conversation.items.clear()
            conversation.snapshot = None
            conversation.title = "New chat"
            self._sidebar.update_title(conversation.id, conversation.title)
            self._chat.clear()
            self._current_assistant = None
            self._current_assistant_item = None
            try:
                self._agent.restore_history(None)
            except Exception:
                logger.exception("Could not clear conversation history.")
            self._update_header()
            return

        self._conversations = [
            item for item in self._conversations if item.id != conversation_id
        ]
        self._sidebar.remove_conversation(conversation_id)
        if conversation.id == self._active_id:
            replacement = self._conversations[0]
            self._active_id = None
            self._sidebar.select(replacement.id)
            self._select_conversation(replacement.id)

    def _update_header(self) -> None:
        conversation = self._active_conversation()
        self._title_label.set_full_text(
            conversation.title if conversation is not None else "New chat"
        )
        provider = provider_display_name(self._provider)
        self._subtitle_label.setText(
            f"{provider}  ·  {self._model}  ·  MCP tools"
        )

    def _derive_title(self, conversation: Conversation, text: str) -> None:
        if conversation.title != "New chat":
            return
        conversation.title = derive_title(text)
        self._sidebar.update_title(conversation.id, conversation.title)
        if conversation.id == self._active_id:
            self._update_header()

    # --------------------------------------------------------------- input

    def _use_suggestion(self, text: str) -> None:
        self.composer.set_text(text)

    def _send_message(self, text: str) -> None:
        if self._busy or self._closing:
            return
        text = text.strip()
        if not text:
            return
        conversation = self._active_conversation()
        if conversation is None:
            return

        self._derive_title(conversation, text)
        item = MessageItem(kind="user", text=text)
        conversation.items.append(item)
        self._chat.add_user(text)
        self.composer.clear()
        self._current_assistant = None
        self._current_assistant_item = None
        self._turn_had_assistant = False
        self._set_busy(True, "Thinking…")
        self._bridge.submit_message(text)

    # ------------------------------------------------------------- stopping

    def _stop_turn(self) -> None:
        """Interrupt the running turn (stop button or Esc)."""

        if not self._busy or self._closing:
            return
        if not self._bridge.cancel_turn():
            # The turn had already finished; its completion is on the way.
            return
        self._set_status("Stopping…", "warning")
        self.composer.set_stopping()

    # -------------------------------------------------------------- events

    def _on_agent_event(self, event: AgentEvent) -> None:
        if self._closing:
            return
        try:
            self._handle_agent_event(event)
        except Exception as exc:
            logger.exception("Failed to display agent event %r.", event)
            self._show_error(
                "Interface error",
                f"Could not display an agent update: {type(exc).__name__}: {exc}",
            )

    def _handle_agent_event(self, event: AgentEvent) -> None:
        conversation = self._active_conversation()
        if conversation is None:
            return
        if not self._busy:
            # Late events from an interrupted turn must not reopen the turn.
            logger.debug("Ignoring a stale agent event: %r", event)
            return

        if isinstance(event, StatusEvent):
            self._set_status(event.text, "busy")
            return

        if isinstance(event, AssistantEvent):
            self._turn_had_assistant = True
            if self._current_assistant_item is None:
                item = MessageItem(kind="assistant", text=event.text)
                conversation.items.append(item)
                self._current_assistant_item = item
                self._current_assistant = self._chat.add_assistant(
                    "", model=self._model
                )
            else:
                item = self._current_assistant_item
                item.text = f"{item.text}\n\n{event.text}".strip()
            self._current_assistant.set_text(self._current_assistant_item.text)
            self._chat.scroll_to_bottom()
            return

        if isinstance(event, ToolCallEvent):
            self._current_assistant = None
            self._current_assistant_item = None
            item = ToolItem(
                name=event.name,
                server=event.server,
                arguments=dict(event.arguments),
            )
            conversation.items.append(item)
            self._chat.add_tool(item)
            self._set_status(f"Running {event.name}…", "busy")
            return

        if isinstance(event, ToolResultEvent):
            self._chat.complete_tool(event.name, event.text, event.is_error)
            if event.is_error:
                self._set_status(f"Tool failed: {event.name}", "warning")
            else:
                self._set_status("Analyzing tool result…", "busy")

    def _on_turn_cancelled(self) -> None:
        if self._closing:
            return
        self._interrupt_running_tools()
        conversation = self._active_conversation()
        if conversation is not None:
            conversation.items.append(
                MessageItem(kind="system", text="Stopped by the user.")
            )
        self._chat.add_system("Stopped by the user.")
        self._current_assistant = None
        self._current_assistant_item = None
        self._turn_had_assistant = False
        self._set_busy(False, "Stopped")
        self._save_snapshot()
        self.composer.focus_input()

    def _interrupt_running_tools(self) -> None:
        """Mark tool cards left in flight by the interruption as stopped."""

        conversation = self._active_conversation()
        if conversation is None:
            return
        for item in conversation.items:
            if not isinstance(item, ToolItem):
                continue
            if item.status not in {"pending", "running"}:
                continue
            self._chat.complete_tool(item.name, "Stopped by the user.", True)
            item.status = "failed"

    def _on_turn_finished(self, result: str) -> None:
        if self._closing:
            return
        conversation = self._active_conversation()
        if conversation is not None and not self._turn_had_assistant and result:
            conversation.items.append(MessageItem(kind="assistant", text=result))
            self._chat.add_assistant(result, model=self._model)
        self._turn_had_assistant = False
        self._mcp_connected = True
        self._mcp_error = ""
        self._set_busy(False, "Ready")
        self._sidebar.set_connection(True)
        self._banner.hide()
        self._refresh_tool_count()
        self._save_snapshot()
        self.composer.focus_input()

    def _on_turn_failed(self, message: str, connection_error: bool) -> None:
        if self._closing:
            return
        conversation = self._active_conversation()
        if conversation is not None:
            conversation.items.append(
                MessageItem(kind="error", title="Request failed", text=message)
            )
        self._set_busy(False, "Error")
        self._chat.add_error("Request failed", message)
        if connection_error:
            self._mcp_connected = False
            self._mcp_error = message
            self._sidebar.set_connection(False, message)
            self._banner.show_message(
                "The MCP server is not available. Check the server and retry."
            )
        self.composer.focus_input()

    # -------------------------------------------------------- connections

    def _on_connection_ready(self) -> None:
        if self._closing:
            return
        self._mcp_connected = True
        self._mcp_error = ""
        self._sidebar.set_connection(True)
        self._banner.hide()
        if not self._busy:
            self._set_status("Ready", "idle")
        self._refresh_tool_count()

    def _on_connection_failed(self, message: str) -> None:
        if self._closing:
            return
        self._mcp_connected = False
        self._mcp_error = message
        self._sidebar.set_connection(False, message)
        if not self._busy:
            self._set_status("MCP unavailable", "warning")
        self._banner.show_message(
            "Could not connect to the configured MCP server. "
            "Fix the server, then retry."
        )

    def _retry_connection(self) -> None:
        if self._busy or self._closing:
            return
        self._banner.hide()
        self._sidebar.set_connection(None)
        self._set_status("Connecting…", "warning")
        self._bridge.start_agent()

    def _refresh_tool_count(self) -> None:
        self._tool_count = int(getattr(self._agent, "tool_count", 0) or 0)
        self._sidebar.set_tool_count(self._tool_count)

    # -------------------------------------------------------- confirmation

    def _on_confirmation_requested(self, request: ConfirmationRequest) -> None:
        if self._closing:
            request.resolve(False)
            return
        try:
            self._chat.mark_tool_pending(request.name)
            QApplication.alert(self)
            self.raise_()
            self.activateWindow()
            dialog = ConfirmToolDialog(
                request.name,
                request.arguments,
                server=self._chat.server_for_running_tool(request.name),
                parent=self,
            )
            dialog.exec()
            approved = bool(dialog.approved and dialog.result() == QDialog.DialogCode.Accepted)
            if approved and dialog.disable_confirmation:
                self._confirm_enabled = False
                self._bridge.set_confirmation_enabled(False)
                self._settings.setValue("confirm_tool_calls", False)
                self._set_status("Tool confirmation disabled for this session", "idle")
            request.resolve(approved)
        except Exception:
            logger.exception("Tool confirmation dialog failed.")
            request.resolve(False)
        finally:
            self._chat.mark_tool_running(request.name)

    # ------------------------------------------------------------ continue

    def _on_continue_requested(self, request: ContinueRequest) -> None:
        if self._closing:
            request.resolve(False)
            return
        try:
            QApplication.alert(self)
            self.raise_()
            self.activateWindow()
            self._set_status("Step budget reached", "warning")
            dialog = ContinueDialog(request.steps, parent=self)
            dialog.exec()
            request.resolve(dialog.approved)
            if dialog.approved:
                self._set_status("Continuing…", "busy")
        except Exception:
            logger.exception("Continue dialog failed.")
            request.resolve(False)

    # ------------------------------------------------------------ settings

    def _current_app_info(self) -> AppInfo:
        """AppInfo updated with the session's live provider and model."""

        options = tuple(
            replace(option, model=self._model)
            if option.provider == self._provider
            else option
            for option in self._info.provider_options
        )
        active = next(
            (option for option in options if option.provider == self._provider),
            None,
        )
        return replace(
            self._info,
            provider=self._provider,
            model=self._model,
            api_key_configured=(
                active.api_key_configured
                if active is not None
                else self._info.api_key_configured
            ),
            providers=options,
        )

    def _open_settings(self) -> None:
        info = self._current_app_info()
        dialog = SettingsDialog(
            info,
            model=self._model,
            mcp_connected=self._mcp_connected,
            mcp_error=self._mcp_error,
            tool_count=self._tool_count,
            confirm_tool_calls=self._confirm_enabled,
            auto_expand_tools=self._auto_expand,
            busy=self._busy,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.result_settings
        provider = result.provider or self._provider

        if provider != self._provider:
            try:
                self._agent.set_provider(provider, result.model)
            except Exception as exc:  # noqa: BLE001 - show the backend error
                QMessageBox.warning(
                    self,
                    "Could not change provider",
                    f"The provider was not changed:\n\n{exc}",
                )
            else:
                self._provider = provider
                self._model = result.model
                self._info = self._current_app_info()
                self._update_header()
                self._set_status(
                    f"Ready · {provider_display_name(provider)}", "idle"
                )
        elif result.model != self._model:
            try:
                self._agent.set_model(result.model)
            except Exception as exc:  # noqa: BLE001 - show the backend error
                QMessageBox.warning(
                    self,
                    "Could not change model",
                    f"The model was not changed:\n\n{exc}",
                )
            else:
                self._model = result.model
                self._info = self._current_app_info()
                self._update_header()
                self._set_status("Ready", "idle")

        self._confirm_enabled = result.confirm_tool_calls
        self._bridge.set_confirmation_enabled(self._confirm_enabled)
        self._settings.setValue("confirm_tool_calls", self._confirm_enabled)

        self._auto_expand = result.auto_expand_tools
        self._chat.set_auto_expand(self._auto_expand)
        self._settings.setValue("auto_expand_tools", self._auto_expand)

    # -------------------------------------------------------------- status

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self._sidebar.set_busy(busy)
        self.composer.set_busy(busy)
        self._set_status(status, "busy" if busy else "idle")

    def _set_status(self, text: str, kind: str) -> None:
        self._status_pill.set_status(text, kind)

    def _show_error(self, title: str, message: str) -> None:
        self._chat.add_error(title, message)
        self._set_status("Error", "error")

    # ---------------------------------------------------------------- chrome

    def _toggle_sidebar(self) -> None:
        visible = self._sidebar.isVisible()
        if visible:
            sizes = self._splitter.sizes()
            if sizes and sizes[0] > 0:
                self._sidebar_width = sizes[0]
            self._sidebar.hide()
        else:
            self._sidebar.show()
            total = max(1, self._splitter.width())
            width = min(self._sidebar_width, max(230, total // 3))
            self._splitter.setSizes([width, max(1, total - width)])
        self._settings.setValue("sidebar_visible", not visible)
        self._sidebar_button.setToolTip(
            "Hide sidebar (Ctrl+B)" if not visible else "Show sidebar (Ctrl+B)"
        )

    def _restore_window_state(self) -> None:
        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter_state = self._settings.value("splitter")
        if splitter_state is not None:
            self._splitter.restoreState(splitter_state)
        else:
            self._splitter.setSizes([270, 910])
        if not _load_bool(self._settings, "sidebar_visible", True):
            self._sidebar.hide()
            self._sidebar_button.setToolTip("Show sidebar (Ctrl+B)")
        # Clamp the default size so the window still fits on small displays.
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            if self.width() > available.width() or self.height() > available.height():
                self.resize(
                    min(1180, available.width()),
                    min(800, available.height()),
                )

    def _save_window_state(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("splitter", self._splitter.saveState())
        self._settings.setValue("sidebar_visible", self._sidebar.isVisible())

    # --------------------------------------------------------------- close

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        if self._busy:
            answer = QMessageBox.question(
                self,
                "Quit while working?",
                "The assistant is still working on a response. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._closing = True
        self._save_snapshot()
        self._save_window_state()
        try:
            self._bridge.shutdown()
        finally:
            event.accept()


def _settings_store():  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QSettings

    return QSettings("autodrc", "autodrc")


def _load_bool(settings, key: str, default: bool) -> bool:  # type: ignore[no-untyped-def]
    value = settings.value(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
