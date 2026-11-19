"""Trabalho em segundo plano com entrega do resultado na thread da interface."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


def _alive(owner: QObject | None) -> bool:
    if owner is None:
        return True
    try:
        import shiboken6

        return shiboken6.isValid(owner)
    except Exception:  # noqa: BLE001
        return True


class Tasks(QObject):
    _finished = Signal(object)

    def __init__(self, workers: int = 4) -> None:
        super().__init__()
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="ventude")
        self._finished.connect(self._deliver)
        self._closed = False

    def run(
        self,
        fn: Callable[..., Any],
        *args: Any,
        done: Callable[[Any], None] | None = None,
        fail: Callable[[BaseException], None] | None = None,
        owner: QObject | None = None,
    ) -> Future[Any] | None:
        if self._closed:
            return None
        future = self._pool.submit(fn, *args)

        def finished(f: Future[Any]) -> None:
            try:
                self._finished.emit((f, done, fail, owner))
            except RuntimeError:  # objeto já destruído no encerramento
                pass

        future.add_done_callback(finished)
        return future

    def _deliver(self, payload: tuple[Any, ...]) -> None:
        future, done, fail, owner = payload
        if future.cancelled() or not _alive(owner):
            return
        error = future.exception()
        try:
            if error is not None:
                if fail:
                    fail(error)
                else:
                    log.warning("Tarefa falhou: %r", error)
            elif done:
                done(future.result())
        except Exception:  # noqa: BLE001
            log.exception("Callback de tarefa falhou")

    def shutdown(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
