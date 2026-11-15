"""Multi-line message composer with automatic growth."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.icons import icon

_MIN_HEIGHT = 42
_MAX_HEIGHT = 170


class ComposerInput(QPlainTextEdit):
    submit_requested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        is_enter = event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        )
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if is_enter and not shift:
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Composer(QWidget):
    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._resize_pending = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.card = QFrame()
        self.card.setObjectName("ComposerCard")
        self.card.setProperty("focused", False)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 8, 10, 8)
        card_layout.setSpacing(2)

        self.input = ComposerInput()
        self.input.setObjectName("ComposerInput")
        self.input.setPlaceholderText("Message Gemini…")
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.input.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.input.setTabChangesFocus(True)
        self.input.setMinimumHeight(_MIN_HEIGHT)
        self.input.setMaximumHeight(_MAX_HEIGHT)
        self.input.submit_requested.connect(self._submit)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.installEventFilter(self)
        card_layout.addWidget(self.input)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(1, 0, 0, 0)
        bottom.setSpacing(8)
        self.hint = QLabel("Enter to send  ·  Shift+Enter for a new line")
        self.hint.setObjectName("ComposerHint")
        bottom.addWidget(self.hint)
        bottom.addStretch(1)

        self.send_button = QToolButton()
        self.send_button.setObjectName("SendButton")
        self.send_button.setIcon(icon("send", "#10131a", 18))
        self.send_button.setIconSize(QSize(18, 18))
        self.send_button.setFixedSize(34, 34)
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.setAccessibleName("Send message")
        self.send_button.clicked.connect(self._submit)
        bottom.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)

        card_layout.addLayout(bottom)
        outer.addWidget(self.card)

        self._update_send_enabled()
        self._schedule_resize()

    # --------------------------------------------------------------- public

    def text(self) -> str:
        return self.input.toPlainText().strip()

    def set_text(self, text: str) -> None:
        self.input.setPlainText(text)
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)
        self.input.setFocus()

    def clear(self) -> None:
        self.input.clear()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.input.setPlaceholderText(
            "Gemini is working…" if busy else "Message Gemini…"
        )
        self._update_send_enabled()
        if not busy:
            self.input.setFocus()

    def focus_input(self) -> None:
        self.input.setFocus()

    # -------------------------------------------------------------- internal

    def _on_text_changed(self) -> None:
        self._update_send_enabled()
        self._schedule_resize()

    def _update_send_enabled(self) -> None:
        self.send_button.setEnabled(bool(self.text()) and not self._busy)

    def _submit(self) -> None:
        if self._busy:
            return
        text = self.text()
        if not text:
            return
        self.submitted.emit(text)

    def _schedule_resize(self) -> None:
        if self._resize_pending:
            return
        self._resize_pending = True
        QTimer.singleShot(0, self._resize_to_content)

    def _resize_to_content(self) -> None:
        self._resize_pending = False
        document_height = self.input.document().size().height()
        height = int(document_height) + 14
        height = max(_MIN_HEIGHT, min(_MAX_HEIGHT, height))
        if height != self.input.height():
            self.input.setFixedHeight(height)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if watched is self.input and event.type() in (
            QEvent.Type.FocusIn,
            QEvent.Type.FocusOut,
        ):
            focused = event.type() == QEvent.Type.FocusIn
            self.card.setProperty("focused", focused)
            style = self.card.style()
            style.unpolish(self.card)
            style.polish(self.card)
        return super().eventFilter(watched, event)
