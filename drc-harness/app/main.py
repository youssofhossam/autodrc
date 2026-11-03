"""Application entry point."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path

from dotenv import load_dotenv

from app.agent.controller import AgentController
from app.config import ConfigError, Settings
from app.gui.chatbot import ChatbotApp
from app.llm.gemini import GeminiClient, GeminiError
from app.mcp_client.client import (
    MCPClient,
    MCPConfigError,
    load_server_configs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    # Explicit path so a desktop launcher works even when the current working
    # directory is not the project directory.
    load_dotenv(PROJECT_ROOT / ".env")

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        servers = load_server_configs(settings.mcp_config_path)
    except MCPConfigError as exc:
        print(f"MCP configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        llm = GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            system_instruction=settings.system_instruction,
            temperature=settings.temperature,
        )
    except GeminiError as exc:
        print(f"Gemini configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        mcp = MCPClient(servers)
    except MCPConfigError as exc:
        print(f"MCP configuration error: {exc}", file=sys.stderr)
        return 2

    agent = AgentController(
        llm,
        mcp,
        max_iterations=settings.max_tool_iterations,
        max_tool_result_chars=settings.max_tool_result_chars,
    )

    try:
        app = ChatbotApp(agent, confirm_tool_calls=settings.confirm_tool_calls)
    except tk.TclError as exc:
        print(
            "Could not start the Tkinter GUI. Make sure a display is available "
            f"(and the python3-tk package is installed): {exc}",
            file=sys.stderr,
        )
        return 1

    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
