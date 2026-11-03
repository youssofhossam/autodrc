"""A small asyncio worker thread for GUI applications.

Tkinter must be driven from the main thread. This worker owns a separate event
loop so the GUI can keep painting, animating, and responding to input while
Gemini and MCP work runs in the background.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AsyncWorker:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="agentic-chatbot-worker", daemon=True
        )
        self._stopped = False
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = [
                task for task in asyncio.all_tasks(self._loop) if not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def submit(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        on_done: Callable[[T], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> concurrent.futures.Future[T]:
        """Schedule a coroutine without blocking the caller."""

        if self._stopped:
            coro.close()
            raise RuntimeError("The background worker has already stopped.")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def dispatch(completed: concurrent.futures.Future[T]) -> None:
            try:
                result = completed.result()
            except concurrent.futures.CancelledError:
                return
            except BaseException as exc:  # noqa: BLE001 - delivered to GUI callback
                if on_error is not None:
                    try:
                        on_error(exc)
                    except Exception:
                        logger.exception("Worker error callback failed.")
                return
            if on_done is not None:
                try:
                    on_done(result)
                except Exception:
                    logger.exception("Worker completion callback failed.")

        future.add_done_callback(dispatch)
        return future

    def stop(self, timeout: float = 5.0) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout)
        if self._thread.is_alive():
            logger.warning("Background worker did not stop within %.1fs.", timeout)
