"""Thành phần nhỏ: LevelMeter, ErrorBanner, APIStatus."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget


class LevelMeter(QProgressBar):
    """Thanh mức âm: -60 dBFS → 0 dBFS, có giữ đỉnh nhẹ. Kèm chữ để không chỉ dựa vào màu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 60)
        self.setTextVisible(False)
        self.setFixedWidth(140)
        self.setAccessibleName("Mức âm thanh")
        self._val = 0.0

    def set_db(self, db: float) -> None:
        v = max(0.0, min(60.0, db + 60.0))
        self._val = max(v, self._val * 0.8)  # nhả chậm
        self.setValue(int(self._val))
        self.setToolTip(f"{db:.0f} dBFS")


class ErrorBanner(QFrame):
    """Banner lỗi/thông tin có chữ + biểu tượng, tự ẩn sau ``timeout_ms`` (0 = giữ)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        self.icon = QLabel()
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.close_btn = QPushButton("Đóng")
        self.close_btn.setAccessibleName("Đóng thông báo")
        self.close_btn.clicked.connect(self.hide)
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.close_btn)
        self._timer = QTimer(self, singleShot=True, timeout=self.hide)
        self.hide()

    def show_message(self, msg: str, kind: str = "error", timeout_ms: int = 0) -> None:
        self.setObjectName("banner_err" if kind == "error" else "banner_info")
        self.icon.setText("⚠" if kind == "error" else "ℹ")
        self.text.setText(msg)
        self.setAccessibleDescription(msg)
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()
        if timeout_ms:
            self._timer.start(timeout_ms)


STATUS_TEXT = {
    "idle": ("○ API: chưa dùng", "muted"),
    "connected": ("● API: đã kết nối", "badge_ok"),
    "retry": ("◐ API: đang thử lại", "badge_warn"),
    "offline": ("✕ Mất mạng — vẫn lưu tiếng Anh; sẽ dịch lại khi có mạng", "badge_err"),
    "disabled": ("○ Dịch: tắt / chưa có API key", "muted"),
    "auth_error": ("✕ API key không hợp lệ", "badge_err"),
    "budget": ("✕ Đã chạm giới hạn chi phí tháng", "badge_err"),
}


class APIStatus(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.set_status("idle")

    def set_status(self, s: str) -> None:
        text, obj = STATUS_TEXT.get(s, (f"API: {s}", "muted"))
        self.status = s
        self.setText(text)
        self.setObjectName(obj)
        self.setAccessibleName(text)
        self.style().unpolish(self)
        self.style().polish(self)


def hspacer() -> QWidget:
    w = QWidget()
    w.setSizePolicy(w.sizePolicy().horizontalPolicy().Expanding, w.sizePolicy().verticalPolicy().Preferred)
    return w


def muted(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("muted")
    lb.setWordWrap(True)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb
