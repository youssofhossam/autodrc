"""Tkinter desktop UI for the agentic chatbot.

Threading model:
  * Tkinter runs on the main thread.
  * Agent/MCP coroutines run on AsyncWorker's event-loop thread.
  * Worker-thread callbacks only put messages on a queue.
  * The main thread polls that queue with `after()` and updates widgets.

No widget is touched from a background thread.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any, Protocol

from app.agent.events import (
    AgentEvent,
    AssistantEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.gui.worker import AsyncWorker

logger = logging.getLogger(__name__)

_MAX_DISPLAY_CHARS = 4_000
_CONFIRM_TIMEOUT_SECONDS = 300


class ChatAgent(Protocol):
    """The small part of the agent the GUI needs; no Gemini/MCP imports."""

    async def start(self) -> None: ...

    async def send_message(
        self, text: str, on_event: Callable[[AgentEvent], None] | None = None
    ) -> str: ...

    async def shutdown(self) -> None: ...

    def clear(self) -> None: ...

    def set_confirmation_callback(
        self, callback: Callable[[str, dict[str, Any]], bool] | None
    ) -> None: ...


class ChatbotApp:
    POLL_INTERVAL_MS = 50

    def __init__(
        self,
        agent: ChatAgent,
        *,
        confirm_tool_calls: bool = True,
        title: str = "DRC Harness",
    ) -> None:
        self._agent = agent
        self._root = tk.Tk()
        self._bus: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._busy = False
        self._connected = False
        self._closing = False
        self._pending_confirmations: list[tuple[threading.Event, dict[str, bool]]] = []
        self._pending_lock = threading.Lock()
        # Plain bool copied from the Tk variable on the main thread. The
        # worker thread reads this instead of touching Tk.
        self._confirm_enabled = bool(confirm_tool_calls)

        self._confirm_var = tk.BooleanVar(value=confirm_tool_calls)
        self._status_var = tk.StringVar(value="Starting...")
        self._build_ui(title)

        self._worker = AsyncWorker()
        self._agent.set_confirmation_callback(self._confirm_tool)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._root.after(self.POLL_INTERVAL_MS, self._drain_bus)
        self._connect()

    # ------------------------------------------------------------------ UI

    def _build_ui(self, title: str) -> None:
        self._root.title(title)
        self._root.geometry("920x720")
        self._root.minsize(680, 480)

        try:
            style = ttk.Style(self._root)
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - unusual Tk builds
            pass

        main = ttk.Frame(self._root, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        self._root.rowconfigure(0, weight=1)
        self._root.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="DRC Harness", font=("Helvetica", 16, "bold")).pack(
            side="left"
        )

        status_frame = ttk.Frame(header)
        status_frame.pack(side="right")
        self._progress = ttk.Progressbar(status_frame, mode="indeterminate", length=110)
        self._progress.pack(side="right", padx=(8, 0))
        ttk.Label(status_frame, textvariable=self._status_var, anchor="e").pack(
            side="right"
        )

        chat_frame = ttk.Frame(main)
        chat_frame.grid(row=1, column=0, sticky="nsew")
        chat_frame.rowconfigure(0, weight=1)
        chat_frame.columnconfigure(0, weight=1)

        self._chat = tk.Text(
            chat_frame,
            wrap="word",
            state="disabled",
            borderwidth=1,
            relief="solid",
            padx=10,
            pady=10,
            font=("Helvetica", 11),
            background="#ffffff",
        )
        scrollbar = ttk.Scrollbar(
            chat_frame, orient="vertical", command=self._chat.yview
        )
        self._chat.configure(yscrollcommand=scrollbar.set)
        self._chat.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self._configure_tags()

        input_frame = ttk.Frame(main)
        input_frame.grid(row=2, column=0, sticky="ew", pady=(8, 4))
        input_frame.columnconfigure(0, weight=1)

        self._input = tk.Text(
            input_frame,
            height=3,
            wrap="word",
            font=("Helvetica", 11),
            borderwidth=1,
            relief="solid",
            padx=8,
            pady=6,
        )
        self._input.grid(row=0, column=0, sticky="nsew")
        self._input.bind("<Return>", self._on_return)

        buttons = ttk.Frame(input_frame)
        buttons.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self._send_button = ttk.Button(
            buttons, text="Send", command=self._on_send, width=12
        )
        self._send_button.pack(fill="x", pady=(0, 6))
        self._clear_button = ttk.Button(
            buttons, text="Clear", command=self._on_clear, width=12
        )
        self._clear_button.pack(fill="x", pady=(0, 6))
        self._confirm_check = ttk.Checkbutton(
            buttons,
            text="Confirm tool calls",
            variable=self._confirm_var,
            command=self._on_confirm_toggle,
        )
        self._confirm_check.pack(anchor="w")

        ttk.Label(
            main,
            text="Enter to send  ·  Shift+Enter for a new line",
            foreground="#666666",
        ).grid(row=3, column=0, sticky="w")

        self._append_message(
            "System",
            "Ask a question. The assistant can use tools discovered from the "
            "configured MCP server. Tool calls are shown here as they happen.",
            "status",
        )

    def _configure_tags(self) -> None:
        palette = {
            "user": "#174f8a",
            "assistant": "#1d5e3a",
            "tool": "#444444",
            "tool_error": "#9b1c1c",
            "error": "#b00020",
            "status": "#666666",
        }
        for kind, color in palette.items():
            self._chat.tag_configure(
                kind,
                foreground=color,
                lmargin1=10,
                lmargin2=10,
                spacing1=2,
                spacing3=3,
            )
            self._chat.tag_configure(
                f"{kind}_name",
                foreground=color,
                font=("Helvetica", 9, "bold"),
                lmargin1=10,
                lmargin2=10,
                spacing1=5,
            )
        self._chat.tag_configure("tool", background="#f3f3f3", font=("Courier", 9))
        self._chat.tag_configure(
            "tool_error", background="#fff0f0", font=("Courier", 9)
        )

    # --------------------------------------------------------------- actions

    def _on_confirm_toggle(self) -> None:
        # Runs on the Tk main thread.
        self._confirm_enabled = bool(self._confirm_var.get())

    def _on_return(self, event: tk.Event) -> str | None:
        # Shift+Enter keeps the default newline behavior.
        if event.state & 0x0001:
            return None
        self._on_send()
        return "break"

    def _on_send(self) -> None:
        if self._busy or self._closing:
            return
        text = self._input.get("1.0", "end").strip()
        if not text:
            return
        self._input.delete("1.0", "end")
        self._append_message("You", text, "user")
        self._submit_message(text)

    def _submit_message(self, text: str) -> None:
        self._set_busy(
            True,
            "Thinking..." if self._connected else "Connecting and thinking...",
        )
        try:
            self._worker.submit(
                self._agent.send_message(text, self._agent_event),
                on_done=lambda _result: self._bus.put(("request_done",)),
                on_error=lambda exc: self._bus.put(("request_error", exc)),
            )
        except RuntimeError as exc:
            self._set_busy(False, "Error")
            self._show_error(str(exc))

    def _on_clear(self) -> None:
        if self._busy or self._closing:
            return
        self._agent.clear()
        self._chat.configure(state="normal")
        self._chat.delete("1.0", "end")
        self._chat.configure(state="disabled")
        self._status_var.set("Ready")
        self._append_message("System", "Conversation cleared.", "status")

    def _connect(self) -> None:
        self._set_busy(True, "Connecting to MCP server...")
        try:
            self._worker.submit(
                self._agent.start(),
                on_done=lambda _result: self._bus.put(("connected",)),
                on_error=lambda exc: self._bus.put(("connect_error", exc)),
            )
        except RuntimeError as exc:
            self._set_busy(False, "Error")
            self._show_error(str(exc))

    # --------------------------------------------------- worker -> GUI queue

    def _agent_event(self, event: AgentEvent) -> None:
        """Called on the worker thread; only touches a thread-safe queue."""

        self._bus.put(("event", event))

    def _drain_bus(self) -> None:
        if self._closing:
            return
        try:
            while True:
                try:
                    message = self._bus.get_nowait()
                except queue.Empty:
                    break
                self._handle_message(message)
        finally:
            if not self._closing:
                self._root.after(self.POLL_INTERVAL_MS, self._drain_bus)

    def _handle_message(self, message: tuple[Any, ...]) -> None:
        kind = message[0]
        try:
            if kind == "event":
                self._handle_agent_event(message[1])
            elif kind == "request_done":
                self._connected = True
                self._set_busy(False, "Ready")
            elif kind == "request_error":
                self._set_busy(False, "Error")
                self._show_error(str(message[1]))
            elif kind == "connected":
                self._connected = True
                self._set_busy(False, "Ready")
            elif kind == "connect_error":
                self._connected = False
                self._set_busy(False, "MCP unavailable — send to retry")
                self._show_error(str(message[1]))
            elif kind == "confirm":
                self._handle_confirmation(
                    message[1], message[2], message[3], message[4]
                )
        except Exception as exc:
            logger.exception("Failed to handle GUI message %r.", kind)
            self._show_error(f"GUI error: {type(exc).__name__}: {exc}")

    def _handle_agent_event(self, event: AgentEvent) -> None:
        if isinstance(event, StatusEvent):
            self._status_var.set(event.text)
        elif isinstance(event, AssistantEvent):
            self._append_message("Assistant", event.text, "assistant")
        elif isinstance(event, ToolCallEvent):
            arguments = json.dumps(
                event.arguments, indent=2, ensure_ascii=False, default=str
            )
            self._append_message(
                "Agent",
                f"Calling tool: {event.name}  (server: {event.server})\n"
                f"Arguments:\n{arguments}",
                "tool",
            )
        elif isinstance(event, ToolResultEvent):
            self._append_message(
                "MCP error" if event.is_error else "MCP",
                f"Tool result: {event.name}\n{_clip(event.text)}",
                "tool_error" if event.is_error else "tool",
            )

    # ------------------------------------------------------------ confirmation

    def _confirm_tool(self, name: str, arguments: dict[str, Any]) -> bool:
        """Called on a worker thread; blocks it until the user answers."""

        if not self._confirm_enabled:
            return True

        event = threading.Event()
        holder: dict[str, bool] = {}
        with self._pending_lock:
            self._pending_confirmations.append((event, holder))
        self._bus.put(("confirm", name, dict(arguments), event, holder))

        if not event.wait(timeout=_CONFIRM_TIMEOUT_SECONDS):
            logger.warning("Timed out waiting for tool confirmation.")
            return False
        return bool(holder.get("approved", False))

    def _handle_confirmation(
        self,
        name: str,
        arguments: dict[str, Any],
        event: threading.Event,
        holder: dict[str, bool],
    ) -> None:
        approved = False
        try:
            if self._closing:
                return
            preview = json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
            if len(preview) > 3_000:
                preview = f"{preview[:3_000]}\n... [truncated]"
            approved = messagebox.askyesno(
                "Confirm MCP tool call",
                "The assistant wants to run an MCP tool.\n\n"
                f"Tool: {name}\n\nArguments:\n{preview}\n\n"
                "Allow this tool call?",
                icon="question",
                parent=self._root,
            )
        finally:
            holder["approved"] = bool(approved)
            event.set()
            with self._pending_lock:
                self._pending_confirmations = [
                    pending
                    for pending in self._pending_confirmations
                    if pending[0] is not event
                ]

    def _deny_pending_confirmations(self) -> None:
        with self._pending_lock:
            pending = list(self._pending_confirmations)
            self._pending_confirmations.clear()
        for event, holder in pending:
            holder["approved"] = False
            event.set()

    # --------------------------------------------------------------- helpers

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        state = ["disabled"] if busy else ["!disabled"]
        self._send_button.state(state)
        self._clear_button.state(state)
        self._status_var.set(status)
        if busy:
            self._progress.start(12)
        else:
            self._progress.stop()

    def _show_error(self, message: str) -> None:
        self._append_message("Error", message, "error")

    def _append_message(self, sender: str, text: str, kind: str) -> None:
        chat = self._chat
        chat.configure(state="normal")
        if chat.index("end-1c") != "1.0":
            chat.insert("end", "\n")
        chat.insert("end", f"[{sender}]\n", f"{kind}_name")
        chat.insert("end", f"{text.rstrip()}\n", kind)
        chat.configure(state="disabled")
        chat.see("end")

    def run(self) -> None:
        self._root.mainloop()

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._deny_pending_confirmations()
        self._set_busy(True, "Shutting down...")
        try:
            future = self._worker.submit(self._agent.shutdown())
            future.result(timeout=8)
        except Exception:
            logger.warning("Agent shutdown did not finish cleanly.", exc_info=True)
        finally:
            self._worker.stop(timeout=3)
            # Drop Tk variables while the interpreter still exists, otherwise
            # their __del__ can run after destroy() and raise a noisy
            # "main thread is not in main loop" warning.
            self._status_var = None  # type: ignore[assignment]
            self._confirm_var = None  # type: ignore[assignment]
            try:
                self._root.destroy()
            except tk.TclError:
                pass


def _clip(text: str, limit: int = _MAX_DISPLAY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [display truncated]"
