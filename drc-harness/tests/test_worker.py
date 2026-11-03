from __future__ import annotations

import asyncio
import threading
import time

from app.gui.worker import AsyncWorker


def test_submit_does_not_block_the_calling_thread() -> None:
    worker = AsyncWorker()
    try:

        async def slow_operation() -> str:
            await asyncio.sleep(0.3)
            return "finished"

        started = time.monotonic()
        future = worker.submit(slow_operation())
        elapsed = time.monotonic() - started

        # Submitting must return immediately; the work belongs to the worker.
        assert elapsed < 0.1
        assert not future.done()
        assert future.result(timeout=3) == "finished"
    finally:
        worker.stop()


def test_done_and_error_callbacks_run() -> None:
    worker = AsyncWorker()
    done_event = threading.Event()
    error_event = threading.Event()
    results: list[str] = []
    errors: list[BaseException] = []
    try:

        async def succeed() -> str:
            await asyncio.sleep(0.02)
            return "ok"

        async def fail() -> None:
            await asyncio.sleep(0.02)
            raise ValueError("expected failure")

        worker.submit(
            succeed(),
            on_done=lambda value: (results.append(value), done_event.set()),
        )
        worker.submit(
            fail(),
            on_error=lambda exc: (errors.append(exc), error_event.set()),
        )

        assert done_event.wait(timeout=2)
        assert error_event.wait(timeout=2)
        assert results == ["ok"]
        assert isinstance(errors[0], ValueError)
    finally:
        worker.stop()
