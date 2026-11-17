"""Dark visual theme for the Qt user interface.

All colours and the stylesheet live here so widgets only use semantic object
names. Keeping styling in one module makes it much easier to adjust the design
without touching behaviour code.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#0f1115",
    "sidebar": "#14171d",
    "panel": "#171a21",
    "panel_alt": "#1b1f28",
    "card": "#171b23",
    "elevated": "#202634",
    "border": "#272c37",
    "border_strong": "#343b49",
    "text": "#e6e9ef",
    "text_soft": "#c2c8d4",
    "text_muted": "#8b93a3",
    "text_faint": "#626a79",
    "accent": "#7c9cf5",
    "accent_hover": "#8fadff",
    "accent_pressed": "#6b87dc",
    "accent_soft": "#26314a",
    "user_bubble": "#26314a",
    "success": "#4cc38a",
    "warning": "#e0b341",
    "danger": "#e5484d",
    "danger_soft": "#2b1719",
    "danger_border": "#5a2529",
    "warning_soft": "#2a2418",
    "warning_border": "#544722",
    "code_bg": "#11141a",
    "code_border": "#262c38",
    "code_text": "#d7e0f2",
    "selection": "#3a4a70",
}

SANS_FAMILIES = (
    "Inter",
    "SF Pro Text",
    "Segoe UI",
    "IBM Plex Sans",
    "Noto Sans",
    "DejaVu Sans",
    "Sans Serif",
)
MONO_FAMILIES = (
    "JetBrains Mono",
    "SF Mono",
    "Cascadia Code",
    "Fira Code",
    "IBM Plex Mono",
    "Noto Sans Mono",
    "DejaVu Sans Mono",
    "Monospace",
)


def _pick_font(families: tuple[str, ...]) -> str:
    available = set(QFontDatabase.families())
    for family in families:
        if family in available:
            return family
    return families[-1]


def sans_family() -> str:
    return _pick_font(SANS_FAMILIES)


def mono_family() -> str:
    return _pick_font(MONO_FAMILIES)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    font = QFont(sans_family(), 10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["panel"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["panel_alt"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["panel"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["selection"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["elevated"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["text_faint"]))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLORS["accent"]))
    app.setPalette(palette)
    app.setStyleSheet(build_stylesheet())


def build_stylesheet() -> str:
    c = COLORS
    mono = mono_family().replace("'", "")
    return f"""
    * {{
        outline: none;
    }}

    QWidget {{
        color: {c["text"]};
        selection-background-color: {c["selection"]};
        selection-color: {c["text"]};
    }}

    QMainWindow, QWidget#AppRoot {{
        background-color: {c["bg"]};
    }}

    QToolTip {{
        background-color: {c["elevated"]};
        color: {c["text"]};
        border: 1px solid {c["border_strong"]};
        border-radius: 6px;
        padding: 5px 8px;
    }}

    /* ------------------------------------------------------------- sidebar */
    QWidget#Sidebar {{
        background-color: {c["sidebar"]};
        border-right: 1px solid {c["border"]};
    }}

    QLabel#BrandTitle {{
        font-size: 11.5pt;
        font-weight: 600;
        color: {c["text"]};
    }}

    QLabel#BrandSubtitle {{
        font-size: 8.5pt;
        color: {c["text_faint"]};
    }}

    QLabel#SectionLabel {{
        font-size: 8pt;
        font-weight: 600;
        color: {c["text_faint"]};
        padding-left: 10px;
    }}

    QPushButton#NewChatButton {{
        background-color: {c["panel_alt"]};
        border: 1px solid {c["border"]};
        border-radius: 9px;
        color: {c["text_soft"]};
        font-weight: 500;
        text-align: left;
        padding: 9px 12px;
    }}

    QPushButton#NewChatButton:hover {{
        background-color: {c["elevated"]};
        border-color: {c["border_strong"]};
        color: {c["text"]};
    }}

    QPushButton#NewChatButton:pressed {{
        background-color: {c["panel"]};
    }}

    QListWidget#ConversationList {{
        background: transparent;
        border: none;
        padding: 0px 6px;
    }}

    QListWidget#ConversationList::item {{
        color: {c["text_muted"]};
        border-radius: 7px;
        padding: 8px 10px;
        margin: 1px 0px;
    }}

    QListWidget#ConversationList::item:hover {{
        background-color: {c["panel_alt"]};
        color: {c["text_soft"]};
    }}

    QListWidget#ConversationList::item:selected {{
        background-color: {c["elevated"]};
        color: {c["text"]};
    }}

    QPushButton#ConnectionStatus {{
        background: transparent;
        border: none;
        border-radius: 7px;
        color: {c["text_faint"]};
        font-size: 8.5pt;
        text-align: left;
        padding: 6px 8px;
    }}

    QPushButton#ConnectionStatus:hover {{
        background-color: {c["panel_alt"]};
        color: {c["text_muted"]};
    }}

    /* ------------------------------------------------------------- top bar */
    QFrame#TopBar {{
        background-color: {c["bg"]};
        border-bottom: 1px solid {c["border"]};
    }}

    QLabel#ConversationTitle {{
        font-size: 11.5pt;
        font-weight: 600;
        color: {c["text"]};
    }}

    QLabel#ConversationSubtitle {{
        font-size: 8.5pt;
        color: {c["text_faint"]};
    }}

    QToolButton#IconButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 6px;
    }}

    QToolButton#IconButton:hover {{
        background-color: {c["panel_alt"]};
        border-color: {c["border"]};
    }}

    QToolButton#IconButton:pressed {{
        background-color: {c["elevated"]};
    }}

    QWidget#StatusPill {{
        background-color: {c["panel_alt"]};
        border: 1px solid {c["border"]};
        border-radius: 11px;
    }}

    QLabel#StatusPillText {{
        color: {c["text_muted"]};
        font-size: 8.5pt;
    }}

    /* ----------------------------------------------------------- chat area */
    QStackedWidget#ChatView, QScrollArea#ChatScroll, QWidget#ChatContent {{
        background-color: {c["bg"]};
        border: none;
    }}

    QLabel#EmptyTitle {{
        font-size: 21pt;
        font-weight: 600;
        color: {c["text"]};
    }}

    QLabel#EmptySubtitle {{
        font-size: 10pt;
        color: {c["text_muted"]};
    }}

    QLabel#EmptyFootnote {{
        font-size: 8.5pt;
        color: {c["text_faint"]};
    }}

    QPushButton#SuggestionChip {{
        background-color: {c["panel"]};
        border: 1px solid {c["border"]};
        border-radius: 10px;
        color: {c["text_soft"]};
        text-align: left;
        padding: 11px 14px;
    }}

    QPushButton#SuggestionChip:hover {{
        background-color: {c["panel_alt"]};
        border-color: {c["border_strong"]};
        color: {c["text"]};
    }}

    QFrame#UserBubble {{
        background-color: {c["user_bubble"]};
        border: 1px solid #303d5b;
        border-radius: 14px;
    }}

    QLabel#UserMessageText {{
        color: #edf2ff;
        font-size: 10.5pt;
    }}

    QLabel#AssistantMeta {{
        color: {c["text_muted"]};
        font-size: 8.5pt;
        font-weight: 600;
    }}

    QLabel#AssistantMetaDetail {{
        color: {c["text_faint"]};
        font-size: 8.5pt;
    }}

    QTextBrowser#MarkdownView {{
        background: transparent;
        border: none;
        padding: 0px;
    }}

    QLabel#SystemMessage {{
        color: {c["text_faint"]};
        font-size: 8.5pt;
    }}

    QFrame#ErrorCard {{
        background-color: {c["danger_soft"]};
        border: 1px solid {c["danger_border"]};
        border-radius: 12px;
    }}

    QLabel#ErrorTitle {{
        color: #ff9a9d;
        font-size: 9.5pt;
        font-weight: 600;
    }}

    QLabel#ErrorText {{
        color: #e8b6b8;
        font-size: 9.5pt;
    }}

    /* ----------------------------------------------------------- tool card */
    QFrame#ToolCard {{
        background-color: {c["card"]};
        border: 1px solid {c["border"]};
        border-radius: 12px;
    }}

    QLabel#ToolKicker {{
        color: {c["text_faint"]};
        font-size: 8pt;
        font-weight: 600;
    }}

    QLabel#ToolName {{
        color: {c["text_soft"]};
        font-family: "{mono}";
        font-size: 9pt;
        font-weight: 600;
    }}

    QLabel#ToolServer {{
        background-color: {c["elevated"]};
        color: {c["text_muted"]};
        border-radius: 5px;
        font-size: 8pt;
        padding: 2px 6px;
    }}

    QLabel#ToolStatus {{
        color: {c["text_faint"]};
        font-size: 8.5pt;
    }}

    QLabel#ToolSummary {{
        color: {c["text_muted"]};
        font-size: 8.5pt;
    }}

    QToolButton#ToolToggle {{
        background: transparent;
        border: none;
        color: {c["text_muted"]};
        font-size: 8.5pt;
        padding: 3px 5px;
        border-radius: 5px;
    }}

    QToolButton#ToolToggle:hover {{
        background-color: {c["elevated"]};
        color: {c["text"]};
    }}

    QLabel#ToolSectionLabel {{
        color: {c["text_faint"]};
        font-size: 8pt;
        font-weight: 600;
    }}

    QPlainTextEdit#ToolText {{
        background-color: {c["code_bg"]};
        border: 1px solid {c["code_border"]};
        border-radius: 7px;
        color: {c["code_text"]};
        font-family: "{mono}";
        font-size: 8.5pt;
        padding: 7px;
    }}

    QLabel#ToolFootNote {{
        color: {c["text_faint"]};
        font-size: 8pt;
    }}

    /* ------------------------------------------------------------- banner */
    QFrame#StatusBanner {{
        background-color: {c["warning_soft"]};
        border-bottom: 1px solid {c["warning_border"]};
    }}

    QLabel#BannerText {{
        color: #e7cf91;
        font-size: 9pt;
    }}

    QPushButton#BannerButton {{
        background-color: transparent;
        border: 1px solid #675627;
        border-radius: 6px;
        color: #f0dca5;
        font-size: 8.5pt;
        padding: 4px 10px;
    }}

    QPushButton#BannerButton:hover {{
        background-color: #3a311b;
    }}

    /* ------------------------------------------------------------ composer */
    QFrame#ComposerCard {{
        background-color: {c["panel"]};
        border: 1px solid {c["border"]};
        border-radius: 14px;
    }}

    QFrame#ComposerCard[focused="true"] {{
        border-color: {c["border_strong"]};
    }}

    QPlainTextEdit#ComposerInput {{
        background: transparent;
        border: none;
        color: {c["text"]};
        font-size: 10.5pt;
        padding: 2px;
    }}

    QLabel#ComposerHint {{
        color: {c["text_faint"]};
        font-size: 8pt;
    }}

    QToolButton#SendButton {{
        background-color: {c["accent"]};
        border: none;
        border-radius: 9px;
    }}

    QToolButton#SendButton:hover {{
        background-color: {c["accent_hover"]};
    }}

    QToolButton#SendButton:pressed {{
        background-color: {c["accent_pressed"]};
    }}

    QToolButton#SendButton:disabled {{
        background-color: {c["panel_alt"]};
        border: 1px solid {c["border"]};
    }}

    QToolButton#StopButton {{
        background-color: {c["panel_alt"]};
        border: 1px solid {c["border_strong"]};
        border-radius: 9px;
    }}

    QToolButton#StopButton:hover {{
        background-color: {c["danger_soft"]};
        border-color: {c["danger_border"]};
    }}

    QToolButton#StopButton:pressed {{
        background-color: {c["danger_border"]};
    }}

    QToolButton#StopButton:disabled {{
        background-color: {c["panel"]};
        border-color: {c["border"]};
    }}

    /* ------------------------------------------------------------- dialogs */
    QDialog {{
        background-color: {c["bg"]};
    }}

    QLabel#DialogTitle {{
        font-size: 16pt;
        font-weight: 600;
        color: {c["text"]};
    }}

    QLabel#DialogSubtitle {{
        font-size: 9.5pt;
        color: {c["text_muted"]};
    }}

    QLabel#SectionTitle {{
        font-size: 9pt;
        font-weight: 600;
        color: {c["text_soft"]};
    }}

    QLabel#FieldLabel {{
        color: {c["text_muted"]};
        font-size: 9pt;
    }}

    QLabel#FieldValue {{
        color: {c["text_soft"]};
        font-size: 9pt;
    }}

    QLabel#HintLabel {{
        color: {c["text_faint"]};
        font-size: 8.5pt;
    }}

    QFrame#DialogCard {{
        background-color: {c["panel"]};
        border: 1px solid {c["border"]};
        border-radius: 10px;
    }}

    QLineEdit#DialogInput, QComboBox#DialogCombo {{
        background-color: {c["panel_alt"]};
        border: 1px solid {c["border"]};
        border-radius: 7px;
        color: {c["text"]};
        padding: 6px 8px;
        min-height: 18px;
    }}

    QLineEdit#DialogInput:focus, QComboBox#DialogCombo:focus {{
        border-color: {c["accent"]};
    }}

    QComboBox#DialogCombo::drop-down {{
        border: none;
        width: 22px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {c["elevated"]};
        border: 1px solid {c["border_strong"]};
        selection-background-color: {c["selection"]};
        padding: 4px;
    }}

    QCheckBox {{
        color: {c["text_soft"]};
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
    }}

    QPushButton#PrimaryButton {{
        background-color: {c["accent"]};
        border: none;
        border-radius: 8px;
        color: #10131a;
        font-weight: 600;
        padding: 8px 16px;
    }}

    QPushButton#PrimaryButton:hover {{
        background-color: {c["accent_hover"]};
    }}

    QPushButton#PrimaryButton:pressed {{
        background-color: {c["accent_pressed"]};
    }}

    QPushButton#SecondaryButton {{
        background-color: {c["panel_alt"]};
        border: 1px solid {c["border"]};
        border-radius: 8px;
        color: {c["text_soft"]};
        padding: 8px 16px;
    }}

    QPushButton#SecondaryButton:hover {{
        background-color: {c["elevated"]};
        border-color: {c["border_strong"]};
        color: {c["text"]};
    }}

    QPushButton#DangerButton {{
        background-color: transparent;
        border: 1px solid {c["danger_border"]};
        border-radius: 8px;
        color: #f09093;
        padding: 8px 16px;
    }}

    QPushButton#DangerButton:hover {{
        background-color: {c["danger_soft"]};
    }}

    QPlainTextEdit#DialogCode {{
        background-color: {c["code_bg"]};
        border: 1px solid {c["code_border"]};
        border-radius: 8px;
        color: {c["code_text"]};
        font-family: "{mono}";
        font-size: 8.5pt;
        padding: 8px;
    }}

    QFrame#Separator {{
        background-color: {c["border"]};
        max-height: 1px;
        min-height: 1px;
    }}

    /* ------------------------------------------------------------ scrolling */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px 2px 2px 0px;
    }}

    QScrollBar::handle:vertical {{
        background-color: #313745;
        min-height: 28px;
        border-radius: 3px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: #414958;
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
    }}

    QScrollBar::handle:horizontal {{
        background-color: #313745;
        min-width: 28px;
        border-radius: 3px;
    }}

    QSplitter::handle {{
        background: transparent;
    }}

    QMenu {{
        background-color: {c["elevated"]};
        border: 1px solid {c["border_strong"]};
        border-radius: 8px;
        padding: 5px;
    }}

    QMenu::item {{
        color: {c["text_soft"]};
        border-radius: 5px;
        padding: 6px 24px 6px 12px;
    }}

    QMenu::item:selected {{
        background-color: {c["selection"]};
        color: {c["text"]};
    }}

    QMessageBox {{
        background-color: {c["bg"]};
    }}
    """
