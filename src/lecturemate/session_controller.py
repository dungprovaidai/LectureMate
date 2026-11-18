"""State machine phiên học + checkpoint + phát hiện phiên ghi dở.

IDLE → CHECKING_DEVICE → RECORDING ↔ PAUSED → STOPPING → COMPLETED
Mọi lỗi thiết bị/mạng dẫn tới ERROR hoặc STOPPING, không làm sập UI.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .domain.models import SessionState
from .persistence.repository import SessionRepository

log = logging.getLogger(__name__)

S = SessionState
ALLOWED: dict[SessionState, set[SessionState]] = {
    S.IDLE: {S.CHECKING_DEVICE},
    S.CHECKING_DEVICE: {S.RECORDING, S.IDLE, S.ERROR},
    S.RECORDING: {S.PAUSED, S.STOPPING, S.ERROR},
    S.PAUSED: {S.RECORDING, S.STOPPING, S.ERROR},
    S.STOPPING: {S.COMPLETED, S.ERROR},
    S.COMPLETED: {S.IDLE},
    S.ERROR: {S.STOPPING, S.IDLE},
    S.RECOVERED: {S.IDLE},
}


class InvalidTransition(RuntimeError):
    pass


class LectureClock:
    """Đồng hồ bài giảng: chỉ chạy khi RECORDING, dừng khi PAUSED."""

    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._acc = 0.0
        self._since: float | None = None

    def start(self) -> None:
        if self._since is None:
            self._since = self._now()

    def pause(self) -> None:
        if self._since is not None:
            self._acc += self._now() - self._since
            self._since = None

    def elapsed_ms(self) -> int:
        live = (self._now() - self._since) if self._since is not None else 0.0
        return int((self._acc + live) * 1000)


class SessionController:
    def __init__(self, repo: SessionRepository, now: Callable[[], float] = time.monotonic,
                 elapsed_fn: Callable[[], int] | None = None):
        """``elapsed_fn``: nguồn thời lượng chuẩn (vd. số mẫu đã ghi vào WAV). Mặc định dùng
        đồng hồ bài giảng (chạy khi RECORDING, dừng khi PAUSED)."""
        self.repo = repo
        self.elapsed_fn = elapsed_fn
        self.state = S.IDLE
        self.session_id: str | None = None
        self.clock = LectureClock(now)
        self._lock = threading.RLock()
        self._listeners: list[Callable[[SessionState, SessionState], None]] = []

    # ------------------------------------------------------------ listeners
    def on_change(self, fn: Callable[[SessionState, SessionState], None]) -> None:
        self._listeners.append(fn)

    def _go(self, new: SessionState) -> None:
        with self._lock:
            old = self.state
            if new not in ALLOWED.get(old, set()):
                raise InvalidTransition(f"{old.value} → {new.value}")
            self.state = new
            if self.session_id and new not in (S.IDLE,):
                self.repo.update_status(self.session_id, new, self.elapsed_ms())
        for fn in list(self._listeners):
            try:
                fn(old, new)
            except Exception:  # listener lỗi không được phá state machine
                log.exception("listener lỗi")

    # -------------------------------------------------------------- actions
    def begin(self, title: str, source_type: str, course_id: str | None = None,
              asr_model: str | None = None) -> str:
        with self._lock:
            if self.state != S.IDLE:
                raise InvalidTransition(f"Không thể bắt đầu khi đang {self.state.value}")
            self.clock = LectureClock(self.clock._now)
            self.session_id = self.repo.create_session(title, source_type, course_id, None, asr_model)
            self._go(S.CHECKING_DEVICE)
            return self.session_id

    def device_ok(self) -> None:
        with self._lock:
            self.clock.start()
            self._go(S.RECORDING)

    def device_failed(self) -> None:
        self._go(S.ERROR)

    def pause(self) -> None:
        with self._lock:
            self.clock.pause()
            self._go(S.PAUSED)

    def resume(self) -> None:
        with self._lock:
            self.clock.start()
            self._go(S.RECORDING)

    def stop(self) -> None:
        with self._lock:
            self.clock.pause()
            self._go(S.STOPPING)

    def complete(self) -> None:
        self._go(S.COMPLETED)

    def reset(self) -> None:
        with self._lock:
            if self.state in (S.COMPLETED, S.ERROR, S.RECOVERED):
                self._go(S.IDLE)
            self.session_id = None

    def checkpoint(self) -> None:
        with self._lock:
            if self.session_id and self.state in (S.RECORDING, S.PAUSED):
                self.repo.checkpoint(self.session_id, self.elapsed_ms())

    def elapsed_ms(self) -> int:
        if self.elapsed_fn is not None:
            return int(self.elapsed_fn())
        return self.clock.elapsed_ms()

    # ------------------------------------------------------------- recovery
    @staticmethod
    def find_interrupted(repo: SessionRepository) -> list[dict]:
        return repo.find_unfinished()

    @staticmethod
    def recover(repo: SessionRepository, session_id: str) -> None:
        """Đóng phiên ghi dở: giữ mọi segment final đã commit, đặt trạng thái RECOVERED.
        Bản dịch đang in_flight được đưa về pending để dịch lại."""
        repo.reset_in_flight()
        repo.update_status(session_id, S.RECOVERED)
