"""Thread-safe bridge between the asyncio worker and the Qt GUI.

The agent and MCP coroutines run on a dedicated asyncio thread. They never
touch widgets. Results and events cross the thread boundary as Qt signals;
Qt automatically delivers them to the main thread through queued connections.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.agent.events import AgentEvent
from app.gui.protocol import ChatAgent
from app.gui.worker import AsyncWorker

logger = logging.getLogger(__name__)

_PROMPT_TIMEOUT_SECONDS = 300


class _BlockingRequest:
    """A question for the GUI that blocks the agent thread until answered.

    Resolution happens on the GUI thread; the waiting agent thread only uses a
    plain ``threading.Event``, never a widget.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._approved = False

    @property
    def approved(self) -> bool:
        return self._approved

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def resolve(self, approved: bool) -> None:
        self._approved = bool(approved)
        self._event.set()


class ConfirmationRequest(_BlockingRequest):
    """A tool call that needs the user's approval."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self.name = name
        self.arguments = dict(arguments)


class ContinueRequest(_BlockingRequest):
    """Ask whether to grant another block of agent steps."""

    def __init__(self, steps: int) -> None:
        super().__init__()
        self.steps = int(steps)


class AgentBridge(QObject):
    event = Signal(object)
    turn_finished = Signal(str)
    turn_failed = Signal(str, bool)
    turn_cancelled = Signal()
    connection_ready = Signal()
    connection_failed = Signal(str)
    confirmation_requested = Signal(object)
    continue_requested = Signal(object)

    def __init__(self, agent: ChatAgent, *, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._agent = agent
        self._worker = AsyncWorker()
        self._pending: set[_BlockingRequest] = set()
        self._pending_lock = threading.Lock()
        self._confirm_enabled = True
        self._closing = False
        self._connected = False
        self._turn_future: concurrent.futures.Future[str] | None = None
        self._turn_task: asyncio.Task[str] | None = None

        self._agent.set_confirmation_callback(self._confirm)
        self._agent.set_continue_callback(self._ask_continue)

    @property
    def connected(self) -> bool:
        return self._connected

    def set_confirmation_enabled(self, enabled: bool) -> None:
        self._confirm_enabled = bool(enabled)

    def start_agent(self) -> None:
        if self._closing:
            return
        try:
            self._worker.submit(
                self._agent.start(),
                on_done=lambda _result: self._connection_done(),
                on_error=lambda exc: self._connection_error(exc),
            )
        except RuntimeError as exc:
            self._connection_error(exc)

    def _connection_done(self) -> None:
        self._connected = True
        self.connection_ready.emit()

    def _connection_error(self, exc: BaseException) -> None:
        self._connected = False
        message = describe_exception(exc)
        logger.warning("MCP connection failed: %s", message)
        self.connection_failed.emit(message)

    def submit_message(self, text: str) -> None:
        if self._closing:
            return
        try:
            self._turn_future = self._worker.submit(
                self._run_turn(text),
                on_done=self._turn_done,
                on_error=self._turn_error,
                on_cancelled=self._turn_cancelled,
            )
        except RuntimeError as exc:
            self.turn_failed.emit(str(exc), False)

    async def _run_turn(self, text: str) -> str:
        """Runs on the worker loop; the task handle lets the GUI cancel it."""

        task = asyncio.current_task()
        self._turn_task = task
        try:
            return await self._agent.send_message(text, self._emit_event)
        finally:
            if self._turn_task is task:
                self._turn_task = None

    def cancel_turn(self) -> bool:
        """Interrupt the running turn.

        The cancellation is delivered on the worker loop, so the turn keeps the
        GUI in its stopping state until the coroutine has actually unwound (and
        released the agent's request lock). Returns True when a request was
        sent; the ``turn_cancelled`` signal follows once the turn ends.
        """

        task = self._turn_task
        if task is not None and not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel)
            logger.info("Cancelling the agent turn.")
            return True
        # The task may not exist yet if the user stops immediately after
        # sending; cancelling the future stops it as soon as it is created.
        future = self._turn_future
        if future is not None:
            return bool(future.cancel())
        return False

    def _emit_event(self, event: AgentEvent) -> None:
        # Called on the worker thread. Signal emission is thread-safe.
        self.event.emit(event)

    def _turn_done(self, result: str | None) -> None:
        self._turn_future = None
        self.turn_finished.emit(result or "")

    def _turn_cancelled(self) -> None:
        self._turn_future = None
        logger.info("Agent turn interrupted by the user.")
        self.turn_cancelled.emit()

    def _turn_error(self, exc: BaseException) -> None:
        self._turn_future = None
        message = describe_exception(exc)
        logger.error(
            "Agent turn failed: %s",
            message,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        self.turn_failed.emit(message, _looks_like_connection_error(exc))

    # ------------------------------------------------------- confirmation

    def _confirm(self, name: str, arguments: dict[str, Any]) -> bool:
        """Runs on the worker thread and blocks until the GUI answers."""

        if self._closing:
            return False
        if not self._confirm_enabled:
            return True

        request = ConfirmationRequest(name, arguments)
        with self._pending_lock:
            if self._closing:
                return False
            self._pending.add(request)
        self.confirmation_requested.emit(request)

        if not request.wait(_PROMPT_TIMEOUT_SECONDS):
            logger.warning("Timed out waiting for tool confirmation: %s", name)
            approved = False
        else:
            approved = request.approved

        with self._pending_lock:
            self._pending.discard(request)
        return approved

    # ------------------------------------------------------------ continue

    def _ask_continue(self, steps: int) -> bool:
        """Runs on the worker thread and blocks until the GUI answers."""

        if self._closing:
            return False

        request = ContinueRequest(steps)
        with self._pending_lock:
            if self._closing:
                return False
            self._pending.add(request)
        self.continue_requested.emit(request)

        if not request.wait(_PROMPT_TIMEOUT_SECONDS):
            logger.warning(
                "Timed out waiting to continue after %s steps.", steps
            )
            approved = False
        else:
            approved = request.approved

        with self._pending_lock:
            self._pending.discard(request)
        return approved

    def _deny_pending(self) -> None:
        with self._pending_lock:
            pending = list(self._pending)
            self._pending.clear()
        for request in pending:
            request.resolve(False)

    # ------------------------------------------------------------ shutdown

    def shutdown(self, timeout: float = 8.0) -> None:
        if self._closing:
            return
        self._closing = True
        self._deny_pending()
        # Abort an in-flight model request before asking the agent to close.
        self.cancel_turn()
        try:
            future = self._worker.submit(self._agent.shutdown())
            future.result(timeout=timeout)
        except Exception:
            logger.warning("Agent shutdown did not finish cleanly.", exc_info=True)
        finally:
            self._worker.stop(timeout=3.0)


def describe_exception(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def _looks_like_connection_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        "connection" in name
        or "mcp server" in text
        or "mcp configuration" in text
    )
