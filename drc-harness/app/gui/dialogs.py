"""Modal dialogs: settings and tool-call approval."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gui.icons import icon
from app.gui.models import AppInfo, ProviderInfo, SettingsResult, provider_display_name
from app.gui.theme import COLORS

_COMMON_MODELS = {
    "gemini": (
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash-lite",
    ),
    "openrouter": (
        "google/gemini-2.5-flash",
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
        "meta-llama/llama-3.3-70b-instruct",
    ),
}


class _ChevronComboBox(QComboBox):
    """Combo box that draws a chevron, making the drop-down affordance explicit."""

    _ARROW_WIDTH = 22

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        pixmap = icon("chevron-down", COLORS["text_muted"], 12).pixmap(12, 12)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = self.width() - self._ARROW_WIDTH + (self._ARROW_WIDTH - 12) // 2
        y = (self.height() - 12) // 2
        painter.drawPixmap(x, y, pixmap)
        painter.end()


class SettingsDialog(QDialog):
    def __init__(
        self,
        info: AppInfo,
        *,
        model: str,
        mcp_connected: bool | None,
        mcp_error: str,
        tool_count: int,
        confirm_tool_calls: bool,
        auto_expand_tools: bool,
        busy: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._info = info
        self._busy = busy
        self._result = SettingsResult(
            model=model,
            confirm_tool_calls=confirm_tool_calls,
            auto_expand_tools=auto_expand_tools,
        )
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(600, 660)
        self.setMinimumSize(500, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        title = QLabel("Settings")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        subtitle = QLabel(
            "Environment configuration is shown here for reference. "
            "Changes in this dialog apply to the current session."
        )
        subtitle.setObjectName("DialogSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(14)

        body_layout.addWidget(self._build_assistant_section(model))
        body_layout.addWidget(
            self._build_tool_section(confirm_tool_calls, auto_expand_tools)
        )
        body_layout.addWidget(
            self._build_mcp_section(mcp_connected, mcp_error, tool_count)
        )
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        footer.setSpacing(8)
        log_hint = QLabel(f"Logs: {Path(info.log_file).name}")
        log_hint.setObjectName("HintLabel")
        log_hint.setToolTip(info.log_file)
        footer.addWidget(log_hint)
        open_logs = QPushButton("Open log folder")
        open_logs.setObjectName("SecondaryButton")
        open_logs.setToolTip(info.log_file)
        open_logs.clicked.connect(self._open_log_folder)
        footer.addWidget(open_logs)
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("SecondaryButton")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = QPushButton("Save")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._accept)
        footer.addWidget(save)
        root.addLayout(footer)

    # ------------------------------------------------------------- sections

    def _build_assistant_section(self, model: str) -> QWidget:
        card, layout = _section_card("Assistant")

        self._provider_models: dict[str, str] = {
            option.provider: option.model for option in self._info.provider_options
        }
        self._provider_models[self._info.provider] = model
        self._last_provider = ""

        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel("Provider")
        label.setObjectName("FieldLabel")
        label.setFixedWidth(130)
        row.addWidget(label)
        self.provider_combo = _ChevronComboBox()
        self.provider_combo.setObjectName("DialogCombo")
        for option in self._selectable_providers():
            self.provider_combo.addItem(option.name, option.provider)
        current = self.provider_combo.findData(self._info.provider)
        self.provider_combo.setCurrentIndex(max(0, current))
        self.provider_combo.setEnabled(not self._busy)
        self.provider_combo.setToolTip(
            "Only providers with an API key configured can be selected."
        )
        self.provider_combo.currentIndexChanged.connect(
            self._on_provider_changed
        )
        row.addWidget(self.provider_combo, 1)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel("Model")
        label.setObjectName("FieldLabel")
        label.setFixedWidth(130)
        row.addWidget(label)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("DialogCombo")
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setEnabled(not self._busy)
        row.addWidget(self.model_combo, 1)
        layout.addLayout(row)

        self._provider_hint = QLabel()
        self._provider_hint.setObjectName("HintLabel")
        self._provider_hint.setWordWrap(True)
        layout.addWidget(self._provider_hint)

        layout.addWidget(_separator())
        key_row = QHBoxLayout()
        key_row.setSpacing(12)
        key_label = QLabel("API key")
        key_label.setObjectName("FieldLabel")
        key_label.setFixedWidth(130)
        key_row.addWidget(key_label)
        self._key_state = QLabel()
        key_row.addWidget(self._key_state, 1)
        layout.addLayout(key_row)

        layout.addLayout(
            _value_row(
                "Temperature",
                "SDK default"
                if self._info.temperature is None
                else f"{self._info.temperature:g}",
            )
        )
        layout.addLayout(
            _value_row("Max tool steps", str(self._info.max_iterations))
        )
        step_hint = QLabel(
            "When a turn uses this many steps, the app asks whether to continue."
        )
        step_hint.setObjectName("HintLabel")
        step_hint.setWordWrap(True)
        layout.addWidget(step_hint)

        self._on_provider_changed()
        return card

    def _selectable_providers(self) -> tuple[ProviderInfo, ...]:
        """Providers with a usable API key, always keeping the active one."""

        options = tuple(
            option
            for option in self._info.provider_options
            if option.api_key_configured or option.provider == self._info.provider
        )
        return options or self._info.provider_options

    def _option_for(self, provider: str) -> ProviderInfo | None:
        return next(
            (
                option
                for option in self._info.provider_options
                if option.provider == provider
            ),
            None,
        )

    def _current_provider(self) -> str:
        value = self.provider_combo.currentData()
        return str(value) if value else self._info.provider

    def _on_provider_changed(self) -> None:
        if self._last_provider:
            self._provider_models[self._last_provider] = (
                self.model_combo.currentText().strip()
            )
        provider = self._current_provider()
        self._last_provider = provider

        option = self._option_for(provider)
        model = self._provider_models.get(provider) or (option.model if option else "")
        common = _COMMON_MODELS.get(provider, ())
        choices = [model] if model else []
        choices.extend(candidate for candidate in common if candidate not in choices)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(choices)
        self.model_combo.setCurrentText(model)
        self.model_combo.blockSignals(False)

        self._update_key_state(provider)
        self._update_provider_hint(provider)

    def _update_key_state(self, provider: str) -> None:
        option = self._option_for(provider)
        configured = (
            option.api_key_configured
            if option is not None
            else provider == self._info.provider and self._info.api_key_configured
        )
        self._key_state.setText(
            "●  Configured · value hidden"
            if configured
            else "●  Not configured"
        )
        self._key_state.setStyleSheet(
            f"color: {COLORS['success'] if configured else COLORS['danger']};"
            "font-size: 9pt;"
        )

    def _update_provider_hint(self, provider: str) -> None:
        provider_hint = (
            " The model must support tool calling."
            if provider == "openrouter"
            else ""
        )
        self._provider_hint.setText(
            f"Model names are passed to {provider_display_name(provider)} as-is. "
            "Changing the provider or model applies to the next message."
            + provider_hint
            + (" Waiting for the current response…" if self._busy else "")
        )

    def _build_tool_section(
        self, confirm_tool_calls: bool, auto_expand_tools: bool
    ) -> QWidget:
        card, layout = _section_card("Tool use")

        self.confirm_check = QCheckBox("Ask before running an MCP tool")
        self.confirm_check.setChecked(confirm_tool_calls)
        layout.addWidget(self.confirm_check)

        self.expand_check = QCheckBox("Expand tool details by default")
        self.expand_check.setChecked(auto_expand_tools)
        layout.addWidget(self.expand_check)

        hint = QLabel(
            "Tool names, descriptions, arguments, and results come from external "
            "MCP servers. They are shown separately from assistant text and are "
            "never treated as instructions."
        )
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return card

    def _build_mcp_section(
        self, connected: bool | None, error: str, tool_count: int
    ) -> QWidget:
        card, layout = _section_card("MCP servers")

        for name in self._info.mcp_servers:
            row = QHBoxLayout()
            row.setSpacing(8)
            state = QLabel()
            state.setFixedSize(8, 8)
            if connected is True:
                colour = COLORS["success"]
            elif connected is False:
                colour = COLORS["danger"]
            else:
                colour = COLORS["warning"]
            state.setStyleSheet(
                f"background-color: {colour}; border-radius: 4px;"
            )
            row.addWidget(state)
            name_label = QLabel(name)
            name_label.setObjectName("FieldValue")
            row.addWidget(name_label, 1)
            status = QLabel(
                "Connected"
                if connected is True
                else "Not connected"
                if connected is False
                else "Connecting…"
            )
            status.setObjectName("HintLabel")
            row.addWidget(status)
            layout.addLayout(row)

        layout.addWidget(_separator())
        discovered = QLabel(
            f"{tool_count} tool{'s' if tool_count != 1 else ''} discovered "
            "during the last request."
            if tool_count
            else "No tools discovered yet. They are refreshed at the start of "
            "every message."
        )
        discovered.setObjectName("FieldValue")
        discovered.setWordWrap(True)
        layout.addWidget(discovered)

        if error:
            error_label = QLabel(error)
            error_label.setObjectName("HintLabel")
            error_label.setWordWrap(True)
            error_label.setStyleSheet(f"color: {COLORS['danger']};")
            layout.addWidget(error_label)

        path = QLabel(self._info.mcp_config_path)
        path.setObjectName("HintLabel")
        path.setWordWrap(True)
        path.setToolTip(self._info.mcp_config_path)
        layout.addWidget(path)
        return card

    # --------------------------------------------------------------- actions

    def _accept(self) -> None:
        model = self.model_combo.currentText().strip()
        if not model:
            QMessageBox.warning(
                self, "Invalid model", "Enter a model name before saving."
            )
            return
        self._result = SettingsResult(
            model=model,
            confirm_tool_calls=self.confirm_check.isChecked(),
            auto_expand_tools=self.expand_check.isChecked(),
            provider=self._current_provider(),
        )
        self.accept()

    def _open_log_folder(self) -> None:
        folder = Path(self._info.log_file).expanduser().parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @property
    def result_settings(self) -> SettingsResult:
        return self._result


class ConfirmToolDialog(QDialog):
    def __init__(
        self,
        name: str,
        arguments: dict,
        *,
        server: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.approved = False
        self.disable_confirmation = False
        self.setWindowTitle("Approve tool call")
        self.setModal(True)
        self.resize(560, 460)
        self.setMinimumSize(460, 340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        icon_label = QLabel()
        icon_label.setPixmap(icon("alert", COLORS["warning"], 24).pixmap(24, 24))
        icon_label.setFixedSize(24, 24)
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("Approve tool call")
        title.setObjectName("DialogTitle")
        heading.addWidget(title)
        subtitle = QLabel(
            "The assistant wants to run a tool on a connected MCP server."
        )
        subtitle.setObjectName("DialogSubtitle")
        subtitle.setWordWrap(True)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        layout.addLayout(header)

        info = QFrame()
        info.setObjectName("DialogCard")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(5)
        info_layout.addLayout(_value_row("Tool", name, mono=True))
        if server:
            info_layout.addLayout(_value_row("Server", server))
        layout.addWidget(info)

        args_label = QLabel("ARGUMENTS")
        args_label.setObjectName("ToolSectionLabel")
        layout.addWidget(args_label)
        details = QPlainTextEdit()
        details.setObjectName("DialogCode")
        details.setReadOnly(True)
        details.setPlainText(
            json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
        )
        details.setMaximumHeight(190)
        layout.addWidget(details, 1)

        warning = QLabel(
            "Only approve calls you understand. Tool descriptions and results "
            "are external data, not trusted instructions."
        )
        warning.setObjectName("HintLabel")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.no_more = QCheckBox("Don't ask again for this session")
        layout.addWidget(self.no_more)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        deny = QPushButton("Deny")
        deny.setObjectName("DangerButton")
        deny.setDefault(True)
        deny.clicked.connect(self.reject)
        buttons.addWidget(deny)
        allow = QPushButton("Allow tool")
        allow.setObjectName("PrimaryButton")
        allow.setAutoDefault(False)
        allow.clicked.connect(self._allow)
        buttons.addWidget(allow)
        layout.addLayout(buttons)

    def _allow(self) -> None:
        self.approved = True
        self.disable_confirmation = self.no_more.isChecked()
        self.accept()


class ContinueDialog(QDialog):
    """Ask whether to keep working after the step budget runs out."""

    def __init__(self, steps: int, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.approved = False
        self.setWindowTitle("Continue working?")
        self.setModal(True)
        self.resize(500, 230)
        self.setMinimumSize(420, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        icon_label = QLabel()
        icon_label.setPixmap(icon("alert", COLORS["warning"], 24).pixmap(24, 24))
        icon_label.setFixedSize(24, 24)
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("Continue working?")
        title.setObjectName("DialogTitle")
        heading.addWidget(title)
        subtitle = QLabel(
            f"The assistant has used its {steps:,}-step budget for this turn "
            "and is still calling tools."
        )
        subtitle.setObjectName("DialogSubtitle")
        subtitle.setWordWrap(True)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        layout.addLayout(header)

        hint = QLabel(
            f"Continuing grants another {steps:,} steps. Long tool loops can "
            "take a while and use more API tokens, so stop if the model looks "
            "stuck repeating itself."
        )
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        stop = QPushButton("Stop")
        stop.setObjectName("SecondaryButton")
        stop.setDefault(True)
        stop.clicked.connect(self.reject)
        buttons.addWidget(stop)
        keep_going = QPushButton("Continue")
        keep_going.setObjectName("PrimaryButton")
        keep_going.setAutoDefault(False)
        keep_going.clicked.connect(self._continue)
        buttons.addWidget(keep_going)
        layout.addLayout(buttons)

    def _continue(self) -> None:
        self.approved = True
        self.accept()


# ------------------------------------------------------------------ helpers


def _section_card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("DialogCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(15, 13, 15, 13)
    layout.setSpacing(9)
    label = QLabel(title)
    label.setObjectName("SectionTitle")
    layout.addWidget(label)
    return card, layout


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("Separator")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def _value_row(label: str, value: str, *, mono: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(12)
    name = QLabel(label)
    name.setObjectName("FieldLabel")
    name.setFixedWidth(130)
    row.addWidget(name)
    text = QLabel(value)
    text.setObjectName("FieldValue")
    text.setWordWrap(True)
    text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if mono:
        text.setStyleSheet(
            f"font-family: '{_mono_font()}'; color: {COLORS['text_soft']};"
        )
    row.addWidget(text, 1)
    return row


def _mono_font() -> str:
    from app.gui.theme import mono_family

    return mono_family()


def show_startup_error(title: str, message: str, details: str = "") -> None:
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    if details:
        box.setDetailedText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
