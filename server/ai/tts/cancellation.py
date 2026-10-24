"""Cooperative cancellation before TTS native work starts."""

from __future__ import annotations

import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager
from typing import Any, Iterator


class SynthesisCancelled(CancelledError):
    """A canceled TTS job skipped pending work or discarded its completed result."""


def check_synthesis_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SynthesisCancelled("TTS request was canceled")


@contextmanager
def synthesis_lock(lock: Any, cancel_event: threading.Event | None) -> Iterator[None]:
    if cancel_event is None:
        with lock:
            yield
        return
    while True:
        check_synthesis_cancelled(cancel_event)
        if lock.acquire(timeout=0.05):
            break
    try:
        check_synthesis_cancelled(cancel_event)
        yield
    finally:
        lock.release()
