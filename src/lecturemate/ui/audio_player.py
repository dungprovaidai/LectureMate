"""Trình phát WAV (QtMultimedia) với thanh tiến độ; chịu được file thiếu/không có backend."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from ..domain.models import format_ts

log = logging.getLogger(__name__)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except Exception:  # thiếu backend multimedia
    QMediaPlayer = None  # type: ignore
    QAudioOutput = None  # type: ignore


class AudioPlayer(QWidget):
    position_changed = Signal(int)  # ms

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.play_btn = QPushButton("▶ Phát")
        self.play_btn.setAccessibleName("Phát hoặc tạm dừng âm thanh")
        self.play_btn.setShortcut("Space")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName("Vị trí phát")
        self.time_lbl = QLabel("00:00:00 / 00:00:00")
        self.time_lbl.setObjectName("ts")
        self.msg = QLabel("")
        self.msg.setObjectName("muted")
        for w in (self.play_btn, self.slider, self.time_lbl, self.msg):
            lay.addWidget(w, 1 if w is self.slider else 0)
        self.available = False
        self.duration = 0
        self._seeking = False
        self.player = None
        if QMediaPlayer is not None:
            self.player = QMediaPlayer(self)
            self.output = QAudioOutput(self)
            self.player.setAudioOutput(self.output)
            self.player.positionChanged.connect(self._on_pos)
            self.player.durationChanged.connect(self._on_dur)
            self.player.playbackStateChanged.connect(self._on_state)
            self.player.errorOccurred.connect(lambda *a: self._set_missing("Không phát được file âm thanh."))
        self.play_btn.clicked.connect(self.toggle)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderReleased.connect(self._slider_released)
        self._set_missing("Chưa có file âm thanh.")

    def _set_missing(self, msg: str) -> None:
        self.available = False
        self.play_btn.setEnabled(False)
        self.slider.setEnabled(False)
        self.msg.setText(msg)

    def load(self, path: str | None, duration_hint_ms: int = 0) -> bool:
        self.stop()
        if not path or not Path(path).exists():
            self._set_missing("Chưa có file âm thanh — transcript vẫn xem được.")
            return False
        if self.player is None:
            self._set_missing("Máy chưa hỗ trợ phát âm thanh (thiếu QtMultimedia).")
            return False
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.available = True
        self.duration = duration_hint_ms
        self.slider.setRange(0, max(1, duration_hint_ms))
        self.time_lbl.setText(f"00:00:00 / {format_ts(duration_hint_ms)}")
        self.play_btn.setEnabled(True)
        self.slider.setEnabled(True)
        self.msg.setText("")
        return True

    def toggle(self) -> None:
        if not self.available or self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        if self.player is not None:
            self.player.stop()

    def seek(self, ms: int, play: bool = True) -> None:
        if not self.available or self.player is None:
            return
        self.player.setPosition(max(0, int(ms)))
        if play:
            self.player.play()

    def position(self) -> int:
        return self.player.position() if self.player is not None else 0

    def _slider_released(self) -> None:
        self._seeking = False
        self.seek(self.slider.value(), play=False)

    def _on_pos(self, ms: int) -> None:
        if not self._seeking:
            self.slider.setValue(ms)
        self.time_lbl.setText(f"{format_ts(ms)} / {format_ts(self.duration)}")
        self.position_changed.emit(ms)

    def _on_dur(self, ms: int) -> None:
        if ms > 0:
            self.duration = ms
            self.slider.setRange(0, ms)

    def _on_state(self, st) -> None:
        playing = QMediaPlayer is not None and st == QMediaPlayer.PlayingState
        self.play_btn.setText("⏸ Tạm dừng" if playing else "▶ Phát")


def segment_at(segments, ms: int):
    """Tìm segment đang phát tại ``ms`` (nhị phân theo start_ms)."""
    lo, hi, best = 0, len(segments) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if segments[mid].start_ms <= ms:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    s = segments[best]
    return s if ms <= s.end_ms + 1500 else None
