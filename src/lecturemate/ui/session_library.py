"""Thư viện buổi học: lọc theo môn/tên, trạng thái, mở chi tiết, xóa có xác nhận."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QStackedLayout, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..domain.models import format_ts


def status_label(s: dict) -> str:
    st = s["status"]
    if st == "recovered":
        return "Phục hồi"
    if st in ("recording", "paused", "checking_device", "stopping"):
        return "Đang ghi"
    if st == "error":
        return "Lỗi"
    n, d = s.get("segment_count") or 0, s.get("translated_count") or 0
    if n and d < n:
        return f"Chưa dịch đủ ({d}/{n})"
    if not n:
        return "Chưa có transcript"
    return "Hoàn thành"


class SessionLibrary(QWidget):
    open_session = Signal(str)
    new_session = Signal()

    def __init__(self, repo, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.repo = repo
        self.rows: list[dict] = []
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        t = QLabel("Thư viện bài giảng")
        t.setObjectName("h1")
        head.addWidget(t, 1)
        self.course = QComboBox()
        self.course.setAccessibleName("Lọc theo môn học")
        self.course.currentIndexChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm theo tên buổi…")
        self.search.textChanged.connect(self.refresh)
        head.addWidget(QLabel("Môn:"))
        head.addWidget(self.course)
        head.addWidget(self.search)
        lay.addLayout(head)

        self.stack = QStackedLayout()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Tên buổi", "Môn", "Ngày", "Thời lượng", "Đoạn", "Trạng thái"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda idx: self._open())
        empty = QWidget()
        el = QVBoxLayout(empty)
        msg = QLabel("Chưa có bài giảng. Tạo buổi mới để bắt đầu.")
        msg.setObjectName("muted")
        msg.setAlignment(Qt.AlignCenter)
        btn = QPushButton("Tạo buổi mới")
        btn.setObjectName("primary")
        btn.clicked.connect(self.new_session)
        el.addStretch(1)
        el.addWidget(msg)
        el.addWidget(btn, 0, Qt.AlignCenter)
        el.addStretch(1)
        self.stack.addWidget(self.table)
        self.stack.addWidget(empty)
        holder = QWidget()
        holder.setLayout(self.stack)
        lay.addWidget(holder, 1)

        bar = QHBoxLayout()
        self.open_btn = QPushButton("Mở")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self._open)
        self.del_btn = QPushButton("Xóa…")
        self.del_btn.setObjectName("danger")
        self.del_btn.clicked.connect(self._delete)
        bar.addStretch(1)
        bar.addWidget(self.open_btn)
        bar.addWidget(self.del_btn)
        lay.addLayout(bar)
        self.reload_courses()

    def reload_courses(self) -> None:
        cur = self.course.currentData()
        self.course.blockSignals(True)
        self.course.clear()
        self.course.addItem("Tất cả", None)
        for c in self.repo.list_courses():
            self.course.addItem(c["name"], c["id"])
        i = self.course.findData(cur)
        self.course.setCurrentIndex(max(0, i))
        self.course.blockSignals(False)

    def refresh(self) -> None:
        self.rows = self.repo.list_sessions(self.course.currentData(), self.search.text().strip() or None)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, s in enumerate(self.rows):
            items = [
                QTableWidgetItem(s["title"]),
                QTableWidgetItem(s.get("course_name") or "—"),
                QTableWidgetItem(s["started_at"][:16].replace("T", " ")),
                QTableWidgetItem(format_ts(s.get("duration_ms") or 0)),
                QTableWidgetItem(str(s.get("segment_count") or 0)),
                QTableWidgetItem(status_label(s)),
            ]
            items[0].setData(Qt.UserRole, s["id"])
            for c, it in enumerate(items):
                self.table.setItem(i, c, it)
        self.table.setSortingEnabled(True)
        has = bool(self.rows) or bool(self.search.text()) or self.course.currentData()
        self.stack.setCurrentIndex(0 if has else 1)

    def selected_id(self) -> str | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        it = self.table.item(r, 0)
        return it.data(Qt.UserRole) if it else None

    def _open(self) -> None:
        sid = self.selected_id()
        if sid:
            self.open_session.emit(sid)

    def _delete(self) -> None:
        sid = self.selected_id()
        if not sid:
            return
        s = self.repo.get_session(sid)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Xóa buổi học")
        box.setText(f"Xóa “{s['title']}”?")
        box.setInformativeText("Thao tác sẽ xóa vĩnh viễn file âm thanh, transcript, bản dịch và ghi chú của "
                               "RIÊNG buổi này. Không thể hoàn tác.")
        box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Yes)
        box.button(QMessageBox.Yes).setText("Xóa vĩnh viễn")
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        ok, msg = self.repo.delete_session(sid)
        if not ok:
            QMessageBox.warning(self, "Chưa xóa xong", msg)
        self.refresh()
