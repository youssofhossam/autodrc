"""Chat presentation widgets: messages, tool activity, and scrolling."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.gui.icons import icon, logo_pixmap
from app.gui.markdown import render_markdown
from app.gui.models import MessageItem, ToolItem, TranscriptItem
from app.gui.theme import COLORS

logger = logging.getLogger(__name__)

_MAX_RESULT_DISPLAY = 8_000
_MAX_ARGUMENT_DISPLAY = 4_000

_SUGGESTIONS = (
    "List the tools available from the connected MCP server.",
    "Inspect the current layout and summarize the top cell.",
    "Run a quick DRC check and explain any violations.",
)


class MarkdownView(QTextBrowser):
    """A read-only rich-text view that grows to fit its document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MarkdownView")
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(18)
        self.document().setDocumentMargin(0)
        self._text = ""
        self._adjust_pending = False

    def set_markdown(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self.setHtml(render_markdown(text))
        self._schedule_adjust()

    @property
    def source_text(self) -> str:
        return self._text

    def _schedule_adjust(self) -> None:
        if self._adjust_pending:
            return
        self._adjust_pending = True
        QTimer.singleShot(0, self._adjust_height)

    def _adjust_height(self) -> None:
        self._adjust_pending = False
        width = self.viewport().width()
        if width <= 20:
            width = max(self.width(), 240)
        self.document().setTextWidth(float(width))
        height = int(self.document().size().height()) + 2
        if height != self.height():
            self.setFixedHeight(max(18, height))

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._schedule_adjust()


class ElidedLabel(QLabel):
    """A label that elides its text but keeps the full value as a tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setText(text)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._refresh()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(20, self.width() - 2)
        text = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, width
        )
        if text != self.text():
            super().setText(text)


class UserMessage(QWidget):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addStretch(1)

        self.bubble = QFrame()
        self.bubble.setObjectName("UserBubble")
        bubble_layout = QVBoxLayout(self.bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(0)

        self.label = QLabel(text)
        self.label.setObjectName("UserMessageText")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bubble_layout.addWidget(self.label)
        layout.addWidget(self.bubble)

    def set_max_width(self, width: int) -> None:
        self.bubble.setMaximumWidth(width)
        # Let short messages stay on one line while still wrapping long ones.
        metrics = self.label.fontMetrics()
        lines = self._text.splitlines() or [self._text]
        content_width = max(
            (metrics.horizontalAdvance(line) for line in lines), default=0
        )
        self.bubble.setMinimumWidth(max(96, min(width, content_width + 36)))


class AssistantMessage(QWidget):
    def __init__(
        self,
        text: str = "",
        *,
        model: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._text = ""
        self._model = model

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        avatar = QLabel()
        avatar.setPixmap(logo_pixmap(26))
        avatar.setFixedSize(26, 26)
        avatar.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(avatar, 0, Qt.AlignmentFlag.AlignTop)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)
        name = QLabel("Assistant")
        name.setObjectName("AssistantMeta")
        header.addWidget(name)
        if model:
            model_label = QLabel(model)
            model_label.setObjectName("AssistantMetaDetail")
            header.addWidget(model_label)
        header.addStretch(1)

        self.copy_button = QToolButton()
        self.copy_button.setObjectName("IconButton")
        self.copy_button.setIcon(icon("copy", COLORS["text_faint"], 14))
        self.copy_button.setIconSize(QSize(14, 14))
        self.copy_button.setFixedSize(24, 24)
        self.copy_button.setToolTip("Copy response")
        self.copy_button.clicked.connect(self.copy_text)
        header.addWidget(self.copy_button)

        content.addLayout(header)

        self.view = MarkdownView()
        content.addWidget(self.view)
        layout.addLayout(content, 1)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self._text = text
        self.view.set_markdown(text)

    def append_text(self, text: str) -> None:
        combined = f"{self._text}\n\n{text}".strip() if self._text else text
        self.set_text(combined)

    def copy_text(self) -> None:
        QApplication.clipboard().setText(self._text)
        QToolTip.showText(QCursor.pos(), "Copied to clipboard", self.copy_button)


class SystemMessage(QWidget):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        label = QLabel(text)
        label.setObjectName("SystemMessage")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)


class ErrorCard(QFrame):
    def __init__(
        self, title: str, text: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ErrorCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 12, 11)
        layout.setSpacing(11)

        icon_label = QLabel()
        icon_label.setPixmap(icon("alert", COLORS["danger"], 18).pixmap(18, 18))
        icon_label.setFixedSize(18, 18)
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(3)
        title_label = QLabel(title or "Something went wrong")
        title_label.setObjectName("ErrorTitle")
        content.addWidget(title_label)
        text_label = QLabel(text)
        text_label.setObjectName("ErrorText")
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        content.addWidget(text_label)
        layout.addLayout(content, 1)

        copy_button = QToolButton()
        copy_button.setObjectName("IconButton")
        copy_button.setIcon(icon("copy", COLORS["danger"], 14))
        copy_button.setIconSize(QSize(14, 14))
        copy_button.setFixedSize(24, 24)
        copy_button.setToolTip("Copy error details")
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(f"{title}\n\n{text}")
        )
        layout.addWidget(copy_button, 0, Qt.AlignmentFlag.AlignTop)


class ToolCard(QFrame):
    """A compact, collapsible record of one MCP tool call."""

    def __init__(
        self,
        item: ToolItem,
        *,
        auto_expand: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("ToolCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)

        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        header.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        kicker = QLabel("TOOL")
        kicker.setObjectName("ToolKicker")
        header.addWidget(kicker)

        self.name_label = ElidedLabel(item.name)
        self.name_label.setObjectName("ToolName")
        header.addWidget(self.name_label, 1)

        if item.server and item.server != "-":
            server = QLabel(item.server)
            server.setObjectName("ToolServer")
            header.addWidget(server)

        self.status_label = QLabel()
        self.status_label.setObjectName("ToolStatus")
        header.addWidget(self.status_label)

        self.toggle = QToolButton()
        self.toggle.setObjectName("ToolToggle")
        self.toggle.setCheckable(True)
        self.toggle.setIcon(icon("chevron-right", COLORS["text_muted"], 14))
        self.toggle.setIconSize(QSize(14, 14))
        self.toggle.setToolTip("Show details")
        self.toggle.toggled.connect(self._on_toggled)
        header.addWidget(self.toggle)
        layout.addLayout(header)

        self.summary = QLabel()
        self.summary.setObjectName("ToolSummary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 3, 0, 0)
        details_layout.setSpacing(5)

        self.argument_label = QLabel("ARGUMENTS")
        self.argument_label.setObjectName("ToolSectionLabel")
        details_layout.addWidget(self.argument_label)
        self.arguments = _code_view()
        self.arguments.setPlainText(_format_arguments(item.arguments))
        self.arguments.setMaximumHeight(130)
        details_layout.addWidget(self.arguments)

        self.result_label = QLabel("RESULT")
        self.result_label.setObjectName("ToolSectionLabel")
        details_layout.addWidget(self.result_label)
        self.result = _code_view()
        self.result.setMaximumHeight(190)
        details_layout.addWidget(self.result)
        self.result_label.hide()
        self.result.hide()

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)
        self.footnote = QLabel()
        self.footnote.setObjectName("ToolFootNote")
        footer.addWidget(self.footnote, 1)
        self.copy_result = QToolButton()
        self.copy_result.setObjectName("ToolToggle")
        self.copy_result.setIcon(icon("copy", COLORS["text_muted"], 13))
        self.copy_result.setIconSize(QSize(13, 13))
        self.copy_result.setToolTip("Copy full result")
        self.copy_result.clicked.connect(self._copy_result)
        self.copy_result.hide()
        footer.addWidget(self.copy_result)
        details_layout.addLayout(footer)

        self.details.hide()
        layout.addWidget(self.details)

        self._expanded = False
        self.set_state(item.status, item.result, item.is_error, item.summary)
        if auto_expand:
            self.toggle.setChecked(True)

    def _on_toggled(self, expanded: bool) -> None:
        self._expanded = expanded
        self.details.setVisible(expanded)
        self.toggle.setIcon(
            icon(
                "chevron-down" if expanded else "chevron-right",
                COLORS["text_muted"],
                14,
            )
        )
        self.toggle.setToolTip("Hide details" if expanded else "Show details")

    def set_state(
        self,
        status: str,
        result: str = "",
        is_error: bool = False,
        summary: str = "",
    ) -> None:
        colour, label = {
            "pending": (COLORS["warning"], "Awaiting approval"),
            "running": (COLORS["accent"], "Running…"),
            "completed": (COLORS["success"], "Completed"),
            "failed": (COLORS["danger"], "Failed"),
        }.get(status, (COLORS["text_faint"], status.title() or "Unknown"))

        self.dot.setStyleSheet(
            f"background-color: {colour}; border-radius: 4px;"
        )
        self.status_label.setText(label)
        self.status_label.setStyleSheet(f"color: {colour};")
        self.item.status = status  # type: ignore[assignment]
        self.item.summary = summary

        if status in {"pending", "running"}:
            self.summary.setText(
                summary or _format_arguments(self.item.arguments, summary=True)
            )
            return

        self.item.result = result
        self.item.is_error = is_error
        display = _truncate(result, _MAX_RESULT_DISPLAY)
        self.result.setPlainText(display or "(empty result)")
        self.result_label.show()
        self.result.show()
        self.copy_result.show()
        self._full_result = result
        final_summary = summary or _summarize_result(result, is_error)
        self.item.summary = final_summary
        self.summary.setText(final_summary)
        self.footnote.setText(_result_note(result))
        self.footnote.show()

        if is_error:
            self.status_label.setText("Failed")
            self.summary.setStyleSheet(f"color: {COLORS['danger']};")
        else:
            self.summary.setStyleSheet("")

    def set_awaiting_approval(self, waiting: bool) -> None:
        if waiting:
            self._pre_approval_status = self.item.status
            self.set_state("pending", summary="Waiting for your approval before running.")
        elif getattr(self, "_pre_approval_status", None) == "running":
            self.set_state("running", summary=self.item.summary)

    def _copy_result(self) -> None:
        QApplication.clipboard().setText(getattr(self, "_full_result", ""))
        QToolTip.showText(QCursor.pos(), "Result copied", self.copy_result)


def _code_view() -> QPlainTextEdit:
    view = QPlainTextEdit()
    view.setObjectName("ToolText")
    view.setReadOnly(True)
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return view


def _format_arguments(arguments: dict, *, summary: bool = False) -> str:
    if not arguments:
        return "No arguments." if summary else "{}"
    if summary:
        parts = []
        for key, value in arguments.items():
            text = json.dumps(value, ensure_ascii=False, default=str)
            parts.append(f"{key}={text}")
            joined = ", ".join(parts)
            if len(joined) > 110:
                return joined[:109] + "…"
        return joined
    text = json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
    return _truncate(text, _MAX_ARGUMENT_DISPLAY)


def _summarize_result(text: str, is_error: bool) -> str:
    clean = (text or "").strip()
    if not clean:
        return "The tool returned an empty result."
    if is_error:
        first = clean.splitlines()[0].strip()
        if len(first) > 120:
            first = first[:119] + "…"
        return f"Error: {first}"
    try:
        parsed = json.loads(clean)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        return f"JSON array · {len(parsed):,} items · {len(clean):,} chars"
    if isinstance(parsed, dict):
        return f"JSON object · {len(parsed):,} keys · {len(clean):,} chars"
    first = clean.splitlines()[0].strip()
    if len(first) > 120:
        first = first[:119] + "…"
    line_count = len(clean.splitlines())
    if line_count > 1:
        return f"{first}  ·  {line_count:,} lines · {len(clean):,} chars"
    return f"{first}  ·  {len(clean):,} chars"


def _result_note(text: str) -> str:
    length = len(text or "")
    if length > _MAX_RESULT_DISPLAY:
        return (
            f"{length:,} characters · showing the first "
            f"{_MAX_RESULT_DISPLAY:,}; the agent received the full result."
        )
    return f"{length:,} characters"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    removed = len(text) - limit
    return f"{text[:limit]}\n... [truncated {removed:,} characters for display]"


class EmptyState(QWidget):
    suggestion_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 28)
        outer.addStretch(1)

        content = QVBoxLayout()
        content.setSpacing(10)
        content.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        logo = QLabel()
        logo.setPixmap(logo_pixmap(54))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(logo)
        content.addSpacing(6)

        title = QLabel("How can I help?")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(title)

        subtitle = QLabel(
            "Ask about your layout, run a check, or explore the tools connected "
            "through MCP."
        )
        subtitle.setObjectName("EmptySubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setFixedWidth(460)
        content.addWidget(subtitle, 0, Qt.AlignmentFlag.AlignHCenter)
        content.addSpacing(16)

        for suggestion in _SUGGESTIONS:
            button = QPushButton(suggestion)
            button.setObjectName("SuggestionChip")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedWidth(430)
            button.clicked.connect(
                lambda _checked=False, text=suggestion: self.suggestion_selected.emit(
                    text
                )
            )
            content.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)

        content.addSpacing(14)
        footnote = QLabel(
            "The assistant can make mistakes. Verify important tool results."
        )
        footnote.setObjectName("EmptyFootnote")
        footnote.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(footnote)

        outer.addLayout(content)
        outer.addStretch(1)


class ChatView(QStackedWidget):
    """Scrollable transcript with a dedicated empty state."""

    suggestion_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatView")

        self._empty = EmptyState()
        self._empty.suggestion_selected.connect(self.suggestion_selected)
        self.addWidget(self._empty)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("ChatScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.verticalScrollBar().setSingleStep(28)

        wrapper = QWidget()
        wrapper.setObjectName("ChatContent")
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)
        wrapper_layout.addStretch(1)

        self._content = QWidget()
        self._content.setObjectName("ChatContent")
        self._content.setMaximumWidth(1000)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(34, 26, 34, 20)
        self._layout.setSpacing(19)
        self._layout.addStretch(1)

        wrapper_layout.addWidget(self._content, 8)
        wrapper_layout.addStretch(1)
        self._scroll.setWidget(wrapper)
        self.addWidget(self._scroll)

        self._messages = 0
        self._user_messages: list[UserMessage] = []
        self._tool_widgets: dict[int, ToolCard] = {}
        self._running_tools: list[tuple[ToolItem, ToolCard]] = []
        self._auto_expand = False
        self._stick_to_bottom = True

        bar = self._scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)

        self.setCurrentIndex(0)

    # --------------------------------------------------------------- state

    @property
    def has_messages(self) -> bool:
        return self._messages > 0

    def set_auto_expand(self, enabled: bool) -> None:
        self._auto_expand = bool(enabled)

    def server_for_running_tool(self, name: str) -> str:
        for item, _card in reversed(self._running_tools):
            if item.name == name:
                return item.server
        return ""

    # -------------------------------------------------------------- adding

    def _insert(self, widget: QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self._messages += 1
        self.setCurrentWidget(self._scroll)
        self.scroll_to_bottom()

    def add_user(self, text: str) -> UserMessage:
        widget = UserMessage(text)
        self._user_messages.append(widget)
        self._insert(widget)
        self._update_user_widths()
        return widget

    def add_assistant(self, text: str = "", *, model: str = "") -> AssistantMessage:
        widget = AssistantMessage(text, model=model)
        self._insert(widget)
        return widget

    def add_system(self, text: str) -> SystemMessage:
        widget = SystemMessage(text)
        self._insert(widget)
        return widget

    def add_error(self, title: str, text: str) -> ErrorCard:
        widget = ErrorCard(title, text)
        self._insert(widget)
        return widget

    def add_tool(self, item: ToolItem) -> ToolCard:
        card = ToolCard(item, auto_expand=self._auto_expand)
        self._tool_widgets[id(item)] = card
        if item.status in {"pending", "running"}:
            self._running_tools.append((item, card))
        elif item.result or item.is_error:
            card.set_state(
                "failed" if item.is_error else "completed",
                item.result,
                item.is_error,
            )
        self._insert(card)
        return card

    def complete_tool(self, name: str, text: str, is_error: bool) -> ToolItem | None:
        for index in range(len(self._running_tools) - 1, -1, -1):
            item, card = self._running_tools[index]
            if item.name != name:
                continue
            item.result = text
            item.is_error = is_error
            card.set_state("failed" if is_error else "completed", text, is_error)
            self._running_tools.pop(index)
            self.scroll_to_bottom()
            return item
        logger.warning("Tool result for '%s' had no matching call card.", name)
        return None

    def mark_tool_pending(self, name: str) -> None:
        for item, card in self._running_tools:
            if item.name == name:
                card.set_awaiting_approval(True)
                return

    def mark_tool_running(self, name: str) -> None:
        for item, card in reversed(self._running_tools):
            if item.name == name:
                card.set_awaiting_approval(False)
                return

    # -------------------------------------------------------------- loading

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._messages = 0
        self._user_messages.clear()
        self._tool_widgets.clear()
        self._running_tools.clear()
        self._stick_to_bottom = True
        self.setCurrentIndex(0)

    def load_items(self, items: Iterable[TranscriptItem]) -> None:
        self.clear()
        for item in items:
            if isinstance(item, MessageItem):
                if item.kind == "user":
                    self.add_user(item.text)
                elif item.kind == "assistant":
                    self.add_assistant(item.text)
                elif item.kind == "error":
                    self.add_error(item.title or "Error", item.text)
                else:
                    self.add_system(item.text)
            elif isinstance(item, ToolItem):
                self.add_tool(item)
        if self._messages:
            self.setCurrentWidget(self._scroll)
            self.scroll_to_bottom(force=True)

    # ------------------------------------------------------------ scrolling

    def scroll_to_bottom(self, force: bool = False) -> None:
        if force:
            self._stick_to_bottom = True
        if not self._stick_to_bottom:
            return
        QTimer.singleShot(0, self._scroll_to_bottom_now)

    def _scroll_to_bottom_now(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick_to_bottom:
            self._scroll.verticalScrollBar().setValue(maximum)

    def _on_value_changed(self, value: int) -> None:
        bar = self._scroll.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum() - 60

    # -------------------------------------------------------------- resizing

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._update_user_widths()

    def _update_user_widths(self) -> None:
        available = self._scroll.viewport().width() or self.width()
        width = max(280, min(760, int(available * 0.72)))
        for widget in self._user_messages:
            widget.set_max_width(width)
