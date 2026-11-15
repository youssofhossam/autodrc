"""Application entry point for the PySide6 desktop client."""

from __future__ import annotations

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Allow both `python -m app.main` and `python app/main.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication, QMessageBox

from app.agent.controller import AgentController
from app.config import ConfigError, Settings
from app.gui.dialogs import show_startup_error
from app.gui.icons import app_icon
from app.gui.models import AppInfo
from app.gui.theme import apply_theme
from app.gui.window import ChatWindow
from app.llm.gemini import GeminiClient, GeminiError
from app.mcp_client.client import MCPClient, MCPConfigError, load_server_configs

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")

    if not _display_available():
        print(
            "Could not start the Qt GUI: no display was detected. "
            "Run the application in a desktop session, or set QT_QPA_PLATFORM.",
            file=sys.stderr,
        )
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("autodrc")
    app.setApplicationDisplayName("autodrc")
    app.setOrganizationName("autodrc")
    app.setWindowIcon(app_icon())
    apply_theme(app)

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO)
        logger.error("Configuration error: %s", exc)
        show_startup_error(
            "autodrc — Configuration error",
            "The application is not configured yet.\n\n"
            f"{exc}\n\n"
            f"Edit {PROJECT_ROOT / '.env'} (start from .env.example) and try again.",
            traceback.format_exc(),
        )
        return 2

    log_file = setup_logging(settings.log_level)
    logger.info("Starting autodrc. Model=%s", settings.gemini_model)

    try:
        servers = load_server_configs(settings.mcp_config_path)
    except MCPConfigError as exc:
        logger.error("MCP configuration error: %s", exc)
        show_startup_error(
            "autodrc — MCP configuration error",
            f"The MCP server configuration could not be loaded.\n\n{exc}",
            traceback.format_exc(),
        )
        return 2

    try:
        llm = GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            system_instruction=settings.system_instruction,
            temperature=settings.temperature,
        )
    except GeminiError as exc:
        logger.error("Gemini configuration error: %s", exc)
        show_startup_error(
            "autodrc — Gemini configuration error",
            f"The Gemini client could not be created.\n\n{exc}",
            traceback.format_exc(),
        )
        return 2

    try:
        mcp = MCPClient(servers)
    except MCPConfigError as exc:
        logger.error("MCP configuration error: %s", exc)
        show_startup_error(
            "autodrc — MCP configuration error",
            f"The MCP client could not be created.\n\n{exc}",
            traceback.format_exc(),
        )
        return 2

    agent = AgentController(
        llm,
        mcp,
        max_iterations=settings.max_tool_iterations,
        max_tool_result_chars=settings.max_tool_result_chars,
    )

    info = AppInfo(
        model=settings.gemini_model,
        api_key_configured=bool(settings.gemini_api_key),
        mcp_servers=tuple(server.name for server in servers),
        mcp_config_path=str(settings.mcp_config_path),
        max_iterations=settings.max_tool_iterations,
        max_result_chars=settings.max_tool_result_chars,
        temperature=settings.temperature,
        log_file=str(log_file),
    )

    window = ChatWindow(
        agent,
        info,
        confirm_tool_calls=settings.confirm_tool_calls,
    )
    _install_exception_hook(window, log_file)
    window.show()
    return app.exec()


def setup_logging(level_name: str) -> Path:
    """Configure console logging plus a rotating file in the user state dir."""

    log_file = _log_path()
    level = getattr(logging, level_name.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [stream_handler]
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError as exc:
        print(f"Could not open the log file {log_file}: {exc}", file=sys.stderr)

    logging.basicConfig(level=level, handlers=handlers)
    return log_file


def _log_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "autodrc" / "app.log"


def _display_available() -> bool:
    if not sys.platform.startswith("linux"):
        return True
    if os.environ.get("QT_QPA_PLATFORM"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _install_exception_hook(window: ChatWindow, log_file: Path) -> None:
    def handle(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical(
            "Unhandled exception", exc_info=(exc_type, exc, tb)
        )
        QMessageBox.critical(
            window,
            "Unexpected error",
            "An unexpected error occurred. The application may still be usable.\n\n"
            f"{exc_type.__name__}: {exc}\n\n"
            f"Technical details were written to:\n{log_file}",
        )

    sys.excepthook = handle


if __name__ == "__main__":
    raise SystemExit(main())
