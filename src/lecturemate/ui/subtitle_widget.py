"""Phụ đề song ngữ: mỗi ``segment_id`` đúng một ``SubtitleRow``.

- partial: chữ nhạt/nghiêng, cập nhật tại chỗ (không nhân bản dòng).
- final: cố định; partial đến muộn cho cùng ID bị bỏ qua.
- bản dịch: pending ("Đang dịch…") / done / failed (nút Thử lại) — không bao giờ hiển thị
  bản chờ như đã xong.
- Cửa sổ hẹp (< 760 px): EN và VI xếp dọc trong cùng thẻ.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QBoxLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..domain.models import TranscriptEvent, TranslationResult, format_ts

PENDING_TEXT = "Đang dịch…"
WAITING_NET_TEXT = "Chờ dịch lại khi có mạng"
FAILED_TEXT = "Chưa dịch được — bản tiếng Anh đã lưu"
NO_KEY_TEXT = "Chưa dịch (chưa có API key)"
SKIPPED_TEXT = "—"


class SubtitleRow(QFrame):
    retry_requested = Signal(str)
    clicked = Signal(str)

    def __init__(self, segment_id: str, parent=None):
        super().__init__(parent)
        self.segment_id = segment_id
        self.is_final = False
        self.version = -1
        self.start_ms = 0
        self.translation_status = "pending"
        self.setObjectName("row")
        self.setFocusPolicy(Qt.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(2)
        top = QHBoxLayout()
        self.ts = QLabel("…")
        self.ts.setObjectName("ts")
        self.state_lbl = QLabel("Tạm")
        self.state_lbl.setObjectName("ts")
        top.addWidget(self.ts)
        top.addStretch(1)
        top.addWidget(self.state_lbl)
        outer.addLayout(top)
        self.body = QBoxLayout(QBoxLayout.LeftToRight)
        self.body.setSpacing(16)
        self.en = QLabel()
        self.en.setWordWrap(True)
        self.en.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.vi = QLabel()
        self.vi.setWordWrap(True)
        self.vi.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for lb in (self.en, self.vi):
            lb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lb.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.retry_btn = QPushButton("Thử lại")
        self.retry_btn.setAccessibleName("Thử dịch lại đoạn này")
        self.retry_btn.clicked.connect(lambda: self.retry_requested.emit(self.segment_id))
        self.retry_btn.hide()
        vi_box = QVBoxLayout()
        vi_box.setSpacing(2)
        vi_box.addWidget(self.vi)
        vi_box.addWidget(self.retry_btn, 0, Qt.AlignLeft)
        self.body.addWidget(self.en, 1)
        self.body.addLayout(vi_box, 1)
        outer.addLayout(self.body)
        self._set_prop("partial", True)

    def mousePressEvent(self, e):
        self.clicked.emit(self.segment_id)
        super().mousePressEvent(e)

    def _set_prop(self, name: str, val: bool) -> None:
        self.setProperty(name, "true" if val else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_narrow(self, narrow: bool) -> None:
        self.body.setDirection(QBoxLayout.TopToBottom if narrow else QBoxLayout.LeftToRight)

    def set_partial(self, ev: TranscriptEvent) -> bool:
        if self.is_final or ev.version <= self.version:
            return False
        self.version = ev.version
        self.start_ms = ev.start_ms
        self.ts.setText(format_ts(ev.start_ms))
        self.en.setObjectName("partial")
        self.en.setText(ev.text_en)
        self.en.style().unpolish(self.en)
        self.en.style().polish(self.en)
        self.state_lbl.setText("Tạm · đang nhận dạng")
        self.vi.setObjectName("pending")
        self.vi.setText("")
        return True

    def set_final(self, ev: TranscriptEvent, translating: bool = True) -> None:
        self.is_final = True
        self.version = max(self.version, ev.version)
        self.start_ms = ev.start_ms
        self.ts.setText(f"{format_ts(ev.start_ms)} – {format_ts(ev.end_ms)}")
        self.en.setObjectName("en")
        self.en.setText(ev.text_en)
        self.en.style().unpolish(self.en)
        self.en.style().polish(self.en)
        self.state_lbl.setText("Đã lưu")
        self._set_prop("partial", False)
        self.setAccessibleName(f"Đoạn {format_ts(ev.start_ms)}: {ev.text_en}")
        if self.translation_status == "pending":
            self.set_translation(None, "pending" if translating else "skipped")

    def set_translation(self, text: str | None, status: str, error_code: str | None = None) -> None:
        self.translation_status = status
        self.retry_btn.hide()
        if status == "done" and text:
            self.vi.setObjectName("vi")
            self.vi.setText(text)
        elif status == "failed":
            self.vi.setObjectName("failed")
            self.vi.setText("⚠ " + FAILED_TEXT)
            self.retry_btn.show()
        elif status == "skipped":
            self.vi.setObjectName("pending")
            self.vi.setText(SKIPPED_TEXT)
        else:
            self.vi.setObjectName("pending")
            if error_code == "no_key":
                self.vi.setText(NO_KEY_TEXT)
            elif error_code in ("offline", "timeout", "server", "rate_limit"):
                self.vi.setText("⟳ " + WAITING_NET_TEXT)
                self.retry_btn.show()
            elif error_code == "budget":
                self.vi.setText("Chưa dịch (đã chạm giới hạn chi phí)")
            else:
                self.vi.setText(PENDING_TEXT)
        self.vi.style().unpolish(self.vi)
        self.vi.style().polish(self.vi)

    def set_match(self, on: bool) -> None:
        self._set_prop("match", on)

    def set_current(self, on: bool) -> None:
        self._set_prop("current", on)


class SubtitleView(QScrollArea):
    retry_requested = Signal(str)
    row_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.container = QWidget()
        self.container.setObjectName("page")
        self.lay = QVBoxLayout(self.container)
        self.lay.setContentsMargins(4, 4, 4, 4)
        self.lay.setSpacing(6)
        self.empty = QLabel("Đang nghe… Chưa phát hiện giọng nói.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.lay.addWidget(self.empty)
        self.lay.addStretch(1)
        self.setWidget(self.container)
        self.rows: dict[str, SubtitleRow] = {}
        self.order: list[str] = []
        self.auto_scroll = True
        self.translating = True
        self._narrow = False
        self._matches: list[str] = []
        self._match_idx = -1
        self._scroll_pending = False

    # ------------------------------------------------------------- rows
    def clear(self) -> None:
        for r in self.rows.values():
            r.setParent(None)
            r.deleteLater()
        self.rows.clear()
        self.order.clear()
        self.empty.show()

    def _row(self, seg_id: str) -> SubtitleRow:
        row = self.rows.get(seg_id)
        if row is None:
            row = SubtitleRow(seg_id)
            row.set_narrow(self._narrow)
            row.retry_requested.connect(self.retry_requested)
            row.clicked.connect(self.row_clicked)
            self.lay.insertWidget(self.lay.count() - 1, row)
            self.rows[seg_id] = row
            self.order.append(seg_id)
            self.empty.hide()
        return row

    def apply_event(self, ev: TranscriptEvent) -> None:
        if ev.type == "discard":
            row = self.rows.get(ev.segment_id)
            if row and not row.is_final:
                self.rows.pop(ev.segment_id)
                self.order.remove(ev.segment_id)
                row.setParent(None)
                row.deleteLater()
                if not self.rows:
                    self.empty.show()
            return
        if ev.type == "partial":
            existing = self.rows.get(ev.segment_id)
            if existing and existing.is_final:
                return  # final bền vững
            self._row(ev.segment_id).set_partial(ev)
        elif ev.type == "final":
            self._row(ev.segment_id).set_final(ev, self.translating)
        self._maybe_scroll()

    def apply_translation(self, segment_id: str, res: TranslationResult) -> None:
        row = self.rows.get(segment_id)
        if row:
            row.set_translation(res.text_vi, res.status.value, res.error_code)

    def load_segments(self, segments) -> None:
        """Nạp các segment đã lưu (khi mở lại / phục hồi)."""
        for s in segments:
            ev = TranscriptEvent("final", s.session_id, s.id, s.start_ms, s.end_ms, s.en_text,
                                 sequence_number=s.sequence_number)
            row = self._row(s.id)
            row.set_final(ev, True)
            row.set_translation(s.vi_text, s.translation_status, s.error_code)

    def final_count(self) -> int:
        return sum(1 for r in self.rows.values() if r.is_final)

    # ----------------------------------------------------------- scroll
    def _maybe_scroll(self) -> None:
        if self.auto_scroll and not self._scroll_pending:
            self._scroll_pending = True
            QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        self._scroll_pending = False
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.viewport().width() < 760
        if narrow != self._narrow:
            self._narrow = narrow
            for r in self.rows.values():
                r.set_narrow(narrow)

    # ----------------------------------------------------------- search
    def find(self, text: str) -> int:
        for sid in self._matches:
            if sid in self.rows:
                self.rows[sid].set_match(False)
        t = text.strip().lower()
        self._matches = [sid for sid in self.order if t and (t in self.rows[sid].en.text().lower()
                                                             or t in self.rows[sid].vi.text().lower())]
        for sid in self._matches:
            self.rows[sid].set_match(True)
        self._match_idx = -1
        if self._matches:
            self.find_next()
        return len(self._matches)

    def find_next(self) -> None:
        if not self._matches:
            return
        self._match_idx = (self._match_idx + 1) % len(self._matches)
        self.ensureWidgetVisible(self.rows[self._matches[self._match_idx]])
