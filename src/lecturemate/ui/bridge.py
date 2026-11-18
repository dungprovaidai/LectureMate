"""Chuyển callback từ thread worker về UI thread (signal Qt) + chạy tác vụ nặng nền."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..app_controller import Callbacks

log = logging.getLogger(__name__)


class Bridge(QObject):
    transcript = Signal(object)
    translation = Signal(str, object)
    level = Signal(float)
    state = Signal(object, object)
    error = Signal(str)
    api_status = Signal(str)
    info = Signal(str)

    def callbacks(self) -> Callbacks:
        # emit() an toàn từ mọi thread; slot chạy ở thread của receiver (UI)
        return Callbacks(
            on_transcript=self.transcript.emit,
            on_translation=self.translation.emit,
            on_level=self.level.emit,
            on_state=self.state.emit,
            on_error=self.error.emit,
            on_api_status=self.api_status.emit,
            on_info=self.info.emit,
        )


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float)


class _Task(QRunnable):
    def __init__(self, fn, signals: _Signals):
        super().__init__()
        self.fn = fn
        self.signals = signals

    def run(self):
        try:
            res = self.fn(self.signals.progress.emit)
        except Exception as exc:
            log.error("Tác vụ nền lỗi: %s", traceback.format_exc())
            self.signals.failed.emit(f"{exc.__class__.__name__}: {exc}")
        else:
            self.signals.done.emit(res)


_alive: set[_Signals] = set()


def run_async(fn: Callable[[Callable[[float], None]], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None,
              on_progress: Callable[[float], None] | None = None) -> None:
    """``fn(progress)`` chạy ở thread pool; callback được gọi trên UI thread."""
    sig = _Signals()
    _alive.add(sig)

    def finish(*_):
        _alive.discard(sig)

    if on_done:
        sig.done.connect(on_done)
    if on_error:
        sig.failed.connect(on_error)
    if on_progress:
        sig.progress.connect(on_progress)
    sig.done.connect(finish)
    sig.failed.connect(finish)
    QThreadPool.globalInstance().start(_Task(fn, sig))
