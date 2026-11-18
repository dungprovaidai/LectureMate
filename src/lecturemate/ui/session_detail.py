"""Chi tiết buổi học: audio player + transcript (click tua, highlight) + Ghi chú AI + Hỏi bài."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget)

from ..ai.notes_generator import NotesGenerator
from ..ai.qa_service import QAService
from ..ai.translation_queue import cost_usd
from ..config import data_dir
from ..domain.models import TranslationResult, TranslationStatus, format_ts, language_name, parse_ts
from ..export_markdown import notes_markdown, safe_filename, transcript_json, transcript_markdown, write_text
from .audio_player import AudioPlayer, segment_at
from .bridge import run_async
from .widgets import muted

STATUS_VI = {"done": "Đã dịch", "pending": "Chờ dịch", "in_flight": "Đang dịch", "failed": "Lỗi dịch", "skipped": "Chưa dịch"}


class SessionDetail(QWidget):
    back = Signal()

    def __init__(self, services, provider_getter, transcriber_factory, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.sv = services
        self.repo = services.repo
        self.provider_getter = provider_getter
        self.transcriber_factory = transcriber_factory
        self.session: dict | None = None
        self.segments = []
        self._current_row = -1
        self._summary_running = False

        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.back_btn = QPushButton("← Thư viện")
        self.back_btn.clicked.connect(self._go_back)
        self.title = QLabel()
        self.title.setObjectName("h1")
        self.meta = muted("")
        self.rename_btn = QPushButton("Đổi tên")
        self.rename_btn.clicked.connect(self._rename)
        head.addWidget(self.back_btn)
        head.addWidget(self.title, 1)
        head.addWidget(self.rename_btn)
        lay.addLayout(head)
        lay.addWidget(self.meta)
        self.player = AudioPlayer()
        self.player.position_changed.connect(self._highlight_at)
        lay.addWidget(self.player)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # ---- transcript tab
        t = QWidget()
        tl = QVBoxLayout(t)
        bar = QHBoxLayout()
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Tìm trong buổi học (Ctrl+F)…")
        self.find_edit.returnPressed.connect(self._find)
        self.retry_btn = QPushButton("Dịch lại các đoạn còn thiếu")
        self.retry_btn.clicked.connect(self._retry_missing)
        self.transcribe_btn = QPushButton("Phiên âm từ file âm thanh")
        self.transcribe_btn.setToolTip("Dùng khi buổi học được ghi ở chế độ chỉ ghi âm, hoặc ASR bị gián đoạn")
        self.transcribe_btn.clicked.connect(self._transcribe_file)
        self.exp_md = QPushButton("Xuất Markdown")
        self.exp_md.clicked.connect(lambda: self._export("md"))
        self.exp_json = QPushButton("Xuất JSON")
        self.exp_json.clicked.connect(lambda: self._export("json"))
        for w in (self.find_edit, self.retry_btn, self.transcribe_btn, self.exp_md, self.exp_json):
            bar.addWidget(w, 1 if w is self.find_edit else 0)
        tl.addLayout(bar)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Mốc", "Người nói / ngôn ngữ", "Nguyên văn", "Tiếng Việt", "Trạng thái", "⚑"])
        self.table.verticalHeader().hide()
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.itemChanged.connect(self._on_item_changed)
        tl.addWidget(self.table, 1)
        tl.addWidget(muted("Click một đoạn để tua âm thanh. Nhấp đúp vào ô Tiếng Việt/Nguyên văn để sửa. Cột ⚑: đánh dấu cần xem lại."))
        self.tabs.addTab(t, "Transcript")

        # ---- full Vietnamese translation tab
        full = QWidget()
        fl = QVBoxLayout(full)
        self.full_translation_status = muted("Bản dịch tiếng Việt theo thứ tự bài giảng.")
        self.full_translation = QTextBrowser()
        self.full_translation.setOpenLinks(False)
        export_full = QPushButton("Xuất bản dịch tổng (.txt)")
        export_full.clicked.connect(self._export_full_translation)
        fl.addWidget(self.full_translation_status)
        fl.addWidget(self.full_translation, 1)
        fl.addWidget(export_full)
        self.tabs.addTab(full, "Dịch tổng")

        # ---- notes tab
        n = QWidget()
        nl = QVBoxLayout(n)
        nbar = QHBoxLayout()
        self.gen_btn = QPushButton("Tạo tóm tắt AI")
        self.gen_btn.setObjectName("primary")
        self.gen_btn.clicked.connect(self._gen_notes)
        self.save_notes_btn = QPushButton("Lưu")
        self.save_notes_btn.clicked.connect(self._save_notes)
        self.exp_notes = QPushButton("Xuất .md")
        self.exp_notes.clicked.connect(self._export_notes)
        self.notes_status = muted("")
        for w in (self.gen_btn, self.save_notes_btn, self.exp_notes):
            nbar.addWidget(w)
        nbar.addWidget(self.notes_status, 1)
        nl.addLayout(nbar)
        split = QSplitter(Qt.Horizontal)
        self.notes_edit = QTextEdit()
        self.notes_edit.setAcceptRichText(False)
        self.notes_edit.setPlaceholderText("Tóm tắt / ghi chú tiếng Việt. Bấm “Tạo tóm tắt AI” hoặc tự viết.")
        self.notes_edit.textChanged.connect(self._preview_notes)
        self.notes_view = QTextBrowser()
        self.notes_view.setOpenLinks(False)
        self.notes_view.anchorClicked.connect(self._on_anchor)
        split.addWidget(self.notes_edit)
        split.addWidget(self.notes_view)
        nl.addWidget(split, 1)
        nl.addWidget(muted("Ghi chú do AI tạo chỉ dựa trên transcript; mốc không có thật bị loại và hiển thị [mốc?]. Hãy kiểm tra lại mục “Cần kiểm tra lại”."))
        self.tabs.addTab(n, "Tóm tắt")

        # ---- QA tab
        q = QWidget()
        ql = QVBoxLayout(q)
        qbar = QHBoxLayout()
        self.q_edit = QLineEdit()
        self.q_edit.setPlaceholderText("Hỏi về bài giảng này, ví dụ: Opportunity cost là gì?")
        self.q_edit.returnPressed.connect(self._ask)
        self.ask_btn = QPushButton("Hỏi")
        self.ask_btn.setObjectName("primary")
        self.ask_btn.clicked.connect(self._ask)
        qbar.addWidget(self.q_edit, 1)
        qbar.addWidget(self.ask_btn)
        ql.addLayout(qbar)
        self.answer = QTextBrowser()
        self.answer.setOpenLinks(False)
        self.answer.anchorClicked.connect(self._on_anchor)
        ql.addWidget(self.answer, 1)
        ql.addWidget(muted("Câu trả lời chỉ dựa trên các đoạn tìm được trong buổi học. Click mốc thời gian để nghe lại."))
        self.tabs.addTab(q, "Hỏi bài")

    # ---------------------------------------------------------------- load
    def load(self, session_id: str) -> None:
        self.session = self.repo.get_session(session_id)
        if not self.session:
            return
        s = self.session
        self.title.setText(s["title"])
        dur = format_ts(s.get("duration_ms") or 0)
        course = s.get("course_name") or "Chưa chọn môn"
        self.meta.setText(f"{course} · {s['started_at'][:16].replace('T', ' ')} UTC · {dur} · nguồn: "
                          f"{'Âm thanh máy' if s['source_type'] == 'loopback' else 'Micro'}")
        self.player.load(s.get("audio_path"), s.get("duration_ms") or 0)
        has_audio = bool(s.get("audio_path")) and Path(s["audio_path"]).exists()
        self.transcribe_btn.setEnabled(has_audio)
        self.reload_segments()
        notes = self.repo.get_notes(session_id)
        self.notes_edit.blockSignals(True)
        self.notes_edit.setPlainText(notes["markdown"] if notes else "")
        self.notes_edit.blockSignals(False)
        self._preview_notes()
        self.notes_status.setText(f"Lưu lúc {notes['updated_at'][:16]}" if notes else "")
        self.answer.clear()
        self.tabs.setCurrentIndex(1)

    def reload_segments(self) -> None:
        if not self.session:
            return
        self.segments = self.repo.list_segments(self.session["id"])
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.segments))
        for i, s in enumerate(self.segments):
            ts = QTableWidgetItem(format_ts(s.start_ms))
            ts.setFlags(ts.flags() & ~Qt.ItemIsEditable)
            ts.setData(Qt.UserRole, s.id)
            speaker = f"Người nói {s.speaker_id} · ước lượng" if s.speaker_id else "Người nói —"
            meta = QTableWidgetItem(f"{speaker}\n{language_name(s.language)}")
            meta.setFlags(meta.flags() & ~Qt.ItemIsEditable)
            en = QTableWidgetItem(s.en_text)
            vi = QTableWidgetItem(s.vi_text or "")
            st = QTableWidgetItem(STATUS_VI.get(s.translation_status, s.translation_status))
            st.setFlags(st.flags() & ~Qt.ItemIsEditable)
            fl = QTableWidgetItem()
            fl.setFlags((fl.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            fl.setCheckState(Qt.Checked if s.flagged else Qt.Unchecked)
            fl.setToolTip("Cần xem lại")
            for c, it in enumerate((ts, meta, en, vi, st, fl)):
                self.table.setItem(i, c, it)
        self.table.resizeRowsToContents()
        self.table.blockSignals(False)
        self._refresh_full_translation()
        missing = sum(1 for s in self.segments if s.translation_status != "done")
        self.retry_btn.setText(f"Dịch lại các đoạn còn thiếu ({missing})")
        self.retry_btn.setEnabled(missing > 0)

    # ------------------------------------------------------------- actions
    def _go_back(self) -> None:
        self.player.stop()
        self.back.emit()

    def _rename(self) -> None:
        if not self.session:
            return
        name, ok = QInputDialog.getText(self, "Đổi tên buổi học", "Tên mới:", text=self.session["title"])
        if ok and name.strip():
            self.repo.rename_session(self.session["id"], name.strip())
            self.load(self.session["id"])

    def _on_cell_clicked(self, row: int, col: int) -> None:
        if col == 5:
            it = self.table.item(row, 5)
            self.repo.set_flag(self.segments[row].id, it.checkState() == Qt.Checked)
            return
        if 0 <= row < len(self.segments):
            self.seek_segment(self.segments[row].start_ms)

    def seek_segment(self, ms: int) -> None:
        self.player.seek(ms)
        self._highlight_at(ms)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        r, c = item.row(), item.column()
        if not (0 <= r < len(self.segments)):
            return
        sid = self.segments[r].id
        if c == 2:
            self.repo.edit_segment(sid, en_text=item.text())
        elif c == 3:
            self.repo.edit_segment(sid, vi_text=item.text())
            st = self.table.item(r, 4)
            if st:
                st.setText(STATUS_VI["done"])
            self._refresh_full_translation()
        elif c == 5:
            self.repo.set_flag(sid, item.checkState() == Qt.Checked)

    def _highlight_at(self, ms: int) -> None:
        seg = segment_at(self.segments, ms)
        if seg is None:
            return
        idx = self.segments.index(seg)
        if idx != self._current_row:
            self._current_row = idx
            self.table.selectRow(idx)
            self.table.scrollToItem(self.table.item(idx, 0), QAbstractItemView.PositionAtCenter)

    def _find(self) -> None:
        t = self.find_edit.text().strip().lower()
        if not t:
            return
        start = self.table.currentRow() + 1
        n = len(self.segments)
        for k in range(n):
            i = (start + k) % n
            s = self.segments[i]
            if t in s.en_text.lower() or t in (s.vi_text or "").lower():
                self.table.selectRow(i)
                self.table.scrollToItem(self.table.item(i, 0))
                return

    def _retry_missing(self) -> None:
        if not self.session:
            return
        if self.provider_getter() is None:
            QMessageBox.information(self, "Chưa có API", "Hãy nhập API key trong Thiết lập trước.")
            return
        n = self.sv.tq.retry_pending(self.session["id"], include_skipped=True)
        self.retry_btn.setText(f"Đang dịch {n} đoạn…")
        self.retry_btn.setEnabled(False)

    def on_translation(self, segment_id: str, res) -> None:
        if res.extra.get("preview"):
            return
        for i, s in enumerate(self.segments):
            if s.id == segment_id:
                self.table.blockSignals(True)
                if res.text_vi:
                    self.table.item(i, 3).setText(res.text_vi)
                self.table.item(i, 4).setText(STATUS_VI.get(res.status.value, res.status.value))
                self.table.blockSignals(False)
                self._refresh_full_translation()
                break

    def _refresh_full_translation(self) -> None:
        if not self.session:
            return
        lines = []
        missing = 0
        for segment in self.repo.list_segments(self.session["id"]):
            text = segment.vi_text
            if not text:
                missing += 1
                text = f"[Chưa dịch: {language_name(segment.language)}]"
            speaker = f"Người nói {segment.speaker_id}: " if segment.speaker_id else ""
            lines.append(f"[{format_ts(segment.start_ms)}] {speaker}{text}")
        self.full_translation.setPlainText("\n\n".join(lines) or "Chưa có transcript.")
        self.full_translation_status.setText(
            f"{len(lines)} đoạn · {missing} đoạn còn thiếu bản dịch" if missing else f"{len(lines)} đoạn · đã dịch xong"
        )

    def _export_full_translation(self) -> None:
        if not self.session:
            return
        default = str(Path.home() / f"{safe_filename(self.session['title'])}-ban-dich-vi.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Xuất bản dịch tiếng Việt", default, "*.txt")
        if path:
            write_text(path, self.full_translation.toPlainText())
            QMessageBox.information(self, "Đã xuất", f"Đã lưu {path}")

    def _transcribe_file(self) -> None:
        if not self.session:
            return
        sid = self.session["id"]
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setText("Đang phiên âm 0%")
        from ..app_controller import transcribe_file_job

        def job(progress):
            tr = self.transcriber_factory()
            if tr is None:
                raise RuntimeError("Chưa cài faster-whisper")
            return transcribe_file_job(self.sv, sid, tr, on_progress=progress)

        def done(n):
            self.transcribe_btn.setText("Phiên âm từ file âm thanh")
            self.transcribe_btn.setEnabled(True)
            QMessageBox.information(self, "Xong", f"Đã thêm {n} đoạn transcript.")
            self.reload_segments()

        def fail(msg):
            self.transcribe_btn.setText("Phiên âm từ file âm thanh")
            self.transcribe_btn.setEnabled(True)
            QMessageBox.warning(self, "Lỗi phiên âm", msg)

        run_async(job, done, fail, lambda p: self.transcribe_btn.setText(f"Đang phiên âm {int(p * 100)}%"))

    def _export(self, kind: str) -> None:
        if not self.session:
            return
        segs = self.repo.list_segments(self.session["id"])
        ext = "md" if kind == "md" else "json"
        default = str(Path.home() / f"{safe_filename(self.session['title'])}.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Xuất transcript", default, f"*.{ext}")
        if not path:
            return
        text = transcript_markdown(self.session, segs) if kind == "md" else transcript_json(self.session, segs)
        write_text(path, text)
        QMessageBox.information(self, "Đã xuất", f"Đã lưu {path}")

    # --------------------------------------------------------------- notes
    def _preview_notes(self) -> None:
        if not self.session:
            return
        md = notes_markdown(self.notes_edit.toPlainText(), self.session, []) if self.notes_edit.toPlainText() else ""
        md = md.split("\n---\n\n## Phụ lục")[0]
        self.notes_view.setMarkdown(md)

    def _gen_notes(self) -> None:
        self._generate_summary(automatic=False)

    def generate_summary_after_session(self) -> None:
        self._generate_summary(automatic=True)

    def _generate_summary(self, automatic: bool) -> None:
        if not self.session:
            return
        if self._summary_running:
            return
        if self.notes_edit.toPlainText().strip():
            if automatic:
                self.notes_status.setText("Đã có tóm tắt/ghi chú; giữ nguyên bản hiện tại.")
                return
            if QMessageBox.question(
                    self, "Tạo lại?", "Tóm tắt/ghi chú hiện tại sẽ được thay bằng bản AI mới. Tiếp tục?") != QMessageBox.Yes:
                return
        provider = self.provider_getter()
        if provider is None:
            if automatic:
                self.notes_status.setText("Chưa tạo tóm tắt: cần API key (Thiết lập → API).")
            else:
                QMessageBox.information(self, "Chưa có API", "Tạo tóm tắt AI cần API key (Thiết lập → API).")
            return
        if automatic and self.sv.cfg.monthly_budget_usd > 0 \
                and self.repo.month_cost_usd() >= self.sv.cfg.monthly_budget_usd:
            self.notes_status.setText("Chưa tạo tóm tắt: đã chạm giới hạn chi phí tháng.")
            return
        segs = self.repo.list_segments(self.session["id"])
        if not segs:
            if automatic:
                self.notes_status.setText("Chưa có transcript để tạo tóm tắt.")
            else:
                QMessageBox.information(self, "Chưa có transcript", "Buổi học chưa có đoạn nào.")
            return
        sid, title = self.session["id"], self.session["title"]

        def under_budget() -> bool:
            budget = self.sv.cfg.monthly_budget_usd
            return budget <= 0 or self.repo.month_cost_usd() < budget

        def record_usage(response) -> None:
            result = TranslationResult("notes", None, TranslationStatus.DONE, model=response.model,
                                       input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                                       elapsed_ms=response.total_ms, first_token_ms=response.ttft_ms)
            cost = cost_usd(response.input_tokens, response.output_tokens,
                            self.sv.cfg.price_in_per_mtok, self.sv.cfg.price_out_per_mtok)
            self.repo.record_request(sid, None, "notes", result, cost)

        gen = NotesGenerator(provider, cache_dir=data_dir() / "notes_cache", budget_check=under_budget,
                             on_usage=record_usage)
        self._summary_running = True
        self.gen_btn.setEnabled(False)
        self.notes_status.setText("Đang tạo tóm tắt… (bài dài có thể mất một lúc)")

        def done(res):
            self._summary_running = False
            self.gen_btn.setEnabled(True)
            try:
                self.repo.save_notes(sid, res.markdown, res.model, res.segment_ids,
                                     "complete" if res.complete else "partial")
            except Exception as exc:
                self.notes_status.setText(f"Tóm tắt đã tạo nhưng chưa lưu được: {exc}")
                return
            if self.session and self.session["id"] == sid:
                self.notes_edit.setPlainText(res.markdown)
                if automatic:
                    self.tabs.setCurrentIndex(2)
                self.notes_status.setText("Đã tạo." if res.complete else
                                          f"Tạo được một phần ({len(res.failed_chunks)} phần lỗi) — bấm lại để chỉ chạy phần lỗi.")

        def fail(msg):
            self._summary_running = False
            self.gen_btn.setEnabled(True)
            self.notes_status.setText("Lỗi: " + msg)

        run_async(lambda p: gen.generate(sid, segs, title), done, fail)

    def _save_notes(self) -> None:
        if self.session:
            self.repo.save_notes(self.session["id"], self.notes_edit.toPlainText(), "manual",
                                 [s.id for s in self.segments], "edited")
            self.notes_status.setText("Đã lưu.")

    def _export_notes(self) -> None:
        if not self.session:
            return
        default = str(Path.home() / f"{safe_filename(self.session['title'])}-ghi-chu.md")
        path, _ = QFileDialog.getSaveFileName(self, "Xuất ghi chú", default, "*.md")
        if path:
            write_text(path, notes_markdown(self.notes_edit.toPlainText(), self.session,
                                            self.repo.list_segments(self.session["id"])))
            QMessageBox.information(self, "Đã xuất", f"Đã lưu {path}")

    def _on_anchor(self, url: QUrl) -> None:
        frag = url.fragment() or url.toString().lstrip("#")
        if frag.startswith("t-") and len(frag) == 8:
            d = frag[2:]
            ms = parse_ts(f"{d[:2]}:{d[2:4]}:{d[4:]}")
        else:
            ms = parse_ts(url.toString().replace("seek:", ""))
        if ms is not None:
            self.tabs.setCurrentIndex(0)
            self.seek_segment(ms)

    # ------------------------------------------------------------------ QA
    def _ask(self) -> None:
        q = self.q_edit.text().strip()
        if not q or not self.session:
            return
        sid = self.session["id"]
        svc = QAService(self.repo, self.provider_getter())
        self.ask_btn.setEnabled(False)
        self.answer.setHtml("<i>Đang tìm trong bài giảng…</i>")

        def done(a):
            self.ask_btn.setEnabled(True)
            body = html.escape(a.text).replace("\n", "<br>")
            import re

            body = re.sub(r"\[(\d{2}:\d{2}:\d{2})\]", r'<a href="seek:\1">[\1]</a>', body)
            parts = [f"<p>{body}</p>"]
            if a.invalid_citations:
                parts.append(f"<p><b>⚠ Đã loại {len(a.invalid_citations)} mốc không có trong bài.</b></p>")
            if a.evidence:
                parts.append("<hr><p><b>Đoạn bài giảng liên quan</b></p><ul>")
                for h in a.evidence:
                    ts = format_ts(h.start_ms)
                    parts.append(f'<li><a href="seek:{ts}">[{ts}]</a> {html.escape(h.en_text)}'
                                 + (f"<br><i>{html.escape(h.vi_text)}</i>" if h.vi_text else "") + "</li>")
                parts.append("</ul>")
            self.answer.setHtml("".join(parts))

        def fail(msg):
            self.ask_btn.setEnabled(True)
            self.answer.setPlainText("Lỗi: " + msg)

        run_async(lambda p: svc.ask(q, sid), done, fail)
