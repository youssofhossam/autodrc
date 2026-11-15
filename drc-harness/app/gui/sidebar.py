"""Collapsible conversation sidebar."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.icons import icon, logo_pixmap
from app.gui.theme import COLORS

_ID_ROLE = Qt.ItemDataRole.UserRole


class Sidebar(QWidget):
    new_chat_requested = Signal()
    conversation_selected = Signal(str)
    rename_requested = Signal(str)
    delete_requested = Signal(str)
    settings_requested = Signal()
    reconnect_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(230)
        self.setMaximumWidth(330)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 12)
        layout.setSpacing(10)

        brand = QHBoxLayout()
        brand.setContentsMargins(4, 0, 4, 2)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(32))
        logo.setFixedSize(32, 32)
        brand.addWidget(logo)

        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(0)
        title = QLabel("autodrc")
        title.setObjectName("BrandTitle")
        subtitle = QLabel("Gemini · MCP tools")
        subtitle.setObjectName("BrandSubtitle")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        brand.addLayout(brand_text, 1)
        layout.addLayout(brand)
        layout.addSpacing(4)

        self.new_chat = QPushButton("  New chat")
        self.new_chat.setObjectName("NewChatButton")
        self.new_chat.setIcon(icon("plus", COLORS["text_soft"], 15))
        self.new_chat.setIconSize(QSize(15, 15))
        self.new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_chat.setToolTip("Start a new conversation (Ctrl+N)")
        self.new_chat.clicked.connect(self.new_chat_requested)
        layout.addWidget(self.new_chat)

        section = QLabel("CONVERSATIONS")
        section.setObjectName("SectionLabel")
        layout.addSpacing(4)
        layout.addWidget(section)

        self.list = QListWidget()
        self.list.setObjectName("ConversationList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.list.setSpacing(0)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._open_context_menu)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        footer.setSpacing(2)
        self.connection = QPushButton("●  Connecting…")
        self.connection.setObjectName("ConnectionStatus")
        self.connection.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connection.setToolTip("Click to retry the MCP connection")
        self.connection.clicked.connect(self.reconnect_requested)
        self.connection.setStyleSheet(f"color: {COLORS['text_faint']};")
        footer.addWidget(self.connection, 1)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("IconButton")
        self.settings_button.setIcon(icon("settings", COLORS["text_muted"], 17))
        self.settings_button.setIconSize(QSize(17, 17))
        self.settings_button.setToolTip("Settings (Ctrl+,)")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(self.settings_requested)
        footer.addWidget(self.settings_button)
        layout.addLayout(footer)

        self._connected: bool | None = None
        self._tool_count = 0

    # ------------------------------------------------------------ list state

    def set_conversations(
        self, conversations: list[tuple[str, str]], active_id: str
    ) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for conversation_id, title in conversations:
            item = QListWidgetItem(title)
            item.setData(_ID_ROLE, conversation_id)
            item.setToolTip(title)
            item.setSizeHint(QSize(0, 38))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.select(active_id)

    def add_conversation(self, conversation_id: str, title: str) -> None:
        self.list.blockSignals(True)
        item = QListWidgetItem(title)
        item.setData(_ID_ROLE, conversation_id)
        item.setToolTip(title)
        item.setSizeHint(QSize(0, 38))
        self.list.insertItem(0, item)
        self.list.blockSignals(False)
        self.select(conversation_id)

    def remove_conversation(self, conversation_id: str) -> None:
        row = self._row_for(conversation_id)
        if row < 0:
            return
        self.list.blockSignals(True)
        self.list.takeItem(row)
        self.list.blockSignals(False)

    def update_title(self, conversation_id: str, title: str) -> None:
        row = self._row_for(conversation_id)
        if row < 0:
            return
        item = self.list.item(row)
        item.setText(title)
        item.setToolTip(title)

    def select(self, conversation_id: str) -> None:
        row = self._row_for(conversation_id)
        if row < 0:
            return
        self.list.blockSignals(True)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        value = item.data(_ID_ROLE)
        return str(value) if value else None

    def set_busy(self, busy: bool) -> None:
        self.list.setEnabled(not busy)
        self.new_chat.setEnabled(not busy)

    # --------------------------------------------------------------- status

    def set_connection(self, connected: bool | None, detail: str = "") -> None:
        self._connected = connected
        if connected is True:
            colour = COLORS["success"]
            text = "MCP connected"
            if self._tool_count:
                text += f" · {self._tool_count} tools"
        elif connected is False:
            colour = COLORS["danger"]
            text = "MCP unavailable"
        else:
            colour = COLORS["warning"]
            text = "Connecting…"
        if detail:
            self.connection.setToolTip(detail)
        else:
            self.connection.setToolTip("Click to retry the MCP connection")
        self.connection.setText(f"●  {text}")
        self.connection.setStyleSheet(f"color: {colour};")

    def set_tool_count(self, count: int) -> None:
        self._tool_count = max(0, count)
        self.set_connection(self._connected, self.connection.toolTip())

    # ---------------------------------------------------------- context menu

    def _row_for(self, conversation_id: str) -> int:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if str(item.data(_ID_ROLE)) == conversation_id:
                return row
        return -1

    def _on_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        value = current.data(_ID_ROLE)
        if value:
            self.conversation_selected.emit(str(value))

    def _open_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        item = self.list.itemAt(position)
        if item is None:
            return
        conversation_id = str(item.data(_ID_ROLE))
        menu = QMenu(self)
        rename = menu.addAction(icon("edit", COLORS["text_muted"], 14), "Rename")
        delete = menu.addAction(icon("trash", COLORS["text_muted"], 14), "Delete")
        action = menu.exec(self.list.viewport().mapToGlobal(position))
        if action is rename:
            self.rename_requested.emit(conversation_id)
        elif action is delete:
            self.delete_requested.emit(conversation_id)
