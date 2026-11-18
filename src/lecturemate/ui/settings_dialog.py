"""Thiết lập: âm thanh/ASR, API (key qua Credential Manager), từ điển thuật ngữ, giao diện."""
from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..ai import secrets
from ..ai.agnes_adapter import AgnesAdapter
from ..ai.glossary import Glossary, Term, sample_glossary
from ..ai.llm_provider import LLMError
from ..audio.sources import list_devices
from ..config import AppConfig
from .bridge import run_async
from .widgets import muted

PRIVACY_TEXT = (
    "• Âm thanh (WAV) và cơ sở dữ liệu chỉ lưu trên máy này (%LOCALAPPDATA%\\LectureMate).\n"
    "• Nhận dạng giọng nói (Faster-Whisper) chạy trên máy, KHÔNG gửi âm thanh lên mạng.\n"
    "• Dịch bằng AI sẽ gửi các đoạn VĂN BẢN không phải tiếng Việt (và câu trước làm ngữ cảnh) qua Internet tới "
    "nhà cung cấp API bạn cấu hình. Ghi chú AI / Hỏi bài cũng gửi văn bản transcript.\n"
    "• API key lưu trong Windows Credential Manager, không nằm trong file cấu hình hay log.\n"
    "• Không đồng bộ đám mây. Bạn có thể xóa từng buổi học (âm thanh + transcript + ghi chú).\n"
    "• Chỉ ghi âm khi được giảng viên/nhà trường cho phép."
)


class GlossaryEditor(QWidget):
    def __init__(self, repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.course = QComboBox()
        self.course.currentIndexChanged.connect(self._load)
        add_c = QPushButton("Thêm môn…")
        add_c.clicked.connect(self._add_course)
        top.addWidget(QLabel("Môn học:"))
        top.addWidget(self.course, 1)
        top.addWidget(add_c)
        lay.addLayout(top)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["EN", "VI (dùng / để ghi nhiều cách dịch)", "Ghi chú"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        lay.addWidget(self.table, 1)
        bar = QHBoxLayout()
        for text, fn in (("Thêm dòng", self._add_row), ("Xóa dòng", self._del_row),
                         ("Nhập CSV…", self._import), ("Mẫu Kinh tế (20 từ)", self._sample),
                         ("Lưu từ điển", self.save)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)
        lay.addWidget(muted("Chỉ thuật ngữ xuất hiện trong câu mới được gửi kèm khi dịch (tiết kiệm token)."))
        self.reload()

    def reload(self) -> None:
        self.course.blockSignals(True)
        self.course.clear()
        for c in self.repo.list_courses():
            self.course.addItem(c["name"], c["id"])
        self.course.blockSignals(False)
        self._load()

    def _load(self) -> None:
        cid = self.course.currentData()
        g = Glossary.from_json(self.repo.get_glossary(cid)) if cid else Glossary()
        self._fill(g)

    def _fill(self, g: Glossary) -> None:
        self.table.setRowCount(len(g.terms))
        for i, t in enumerate(g.terms):
            for c, v in enumerate((t.en, t.vi, t.note)):
                self.table.setItem(i, c, QTableWidgetItem(v))

    def _collect(self) -> Glossary:
        terms = []
        for r in range(self.table.rowCount()):
            vals = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else "") for c in range(3)]
            if vals[0] and vals[1]:
                terms.append(Term(*vals))
        return Glossary(terms)

    def _add_course(self) -> None:
        name, ok = QInputDialog.getText(self, "Thêm môn học", "Tên môn:")
        if ok and name.strip():
            cid = self.repo.upsert_course(name.strip())
            self.reload()
            self.course.setCurrentIndex(self.course.findData(cid))

    def _add_row(self) -> None:
        self.table.insertRow(self.table.rowCount())

    def _del_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Nhập từ điển", str(Path.home()), "CSV/TSV (*.csv *.tsv *.txt)")
        if path:
            try:
                g = Glossary.from_csv(Path(path).read_text(encoding="utf-8-sig"))
            except Exception as exc:
                QMessageBox.warning(self, "Lỗi", f"Không đọc được file: {exc}")
                return
            cur = self._collect()
            self._fill(Glossary(cur.terms + g.terms))

    def _sample(self) -> None:
        cur = self._collect()
        have = {t.en.lower() for t in cur.terms}
        self._fill(Glossary(cur.terms + [t for t in sample_glossary().terms if t.en.lower() not in have]))

    def save(self) -> None:
        cid = self.course.currentData()
        if not cid:
            QMessageBox.information(self, "Chưa có môn", "Hãy thêm môn học trước.")
            return
        self.repo.set_glossary(cid, self._collect().to_json())


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, repo, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Thiết lập — LectureMate")
        self.resize(760, 560)
        self.cfg = cfg
        self.repo = repo
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # ---------------- Âm thanh & ASR
        a = QWidget()
        f = QFormLayout(a)
        self.source = QComboBox()
        self.source.addItem("Micro", "microphone")
        self.source.addItem("Âm thanh máy (WASAPI loopback)", "loopback")
        self.source.setCurrentIndex(max(0, self.source.findData(cfg.source_type)))
        self.asr_model = QComboBox()
        for m in ("tiny", "base", "small", "medium"):
            self.asr_model.addItem(m)
        self.asr_model.setCurrentText(cfg.asr_model)
        self.asr_device = QComboBox()
        for d in ("auto", "cpu", "cuda"):
            self.asr_device.addItem(d)
        self.asr_device.setCurrentText(cfg.asr_device)
        self.translation_mode = QComboBox()
        self.translation_mode.addItem("Nhanh — cập nhật khi có thêm ~2 từ (nhiều lượt API hơn)", "fast")
        self.translation_mode.addItem("Tự nhiên — chờ hết cụm/câu, dịch ổn định hơn", "natural")
        self.translation_mode.setCurrentIndex(max(0, self.translation_mode.findData(cfg.translation_mode)))
        self.mode = QComboBox()
        self.mode.addItem("Trực tiếp (phụ đề realtime)", "realtime")
        self.mode.addItem("Chỉ ghi âm — phiên âm & dịch sau buổi (máy yếu)", "record_only")
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg.asr_mode)))
        self.save_audio = QCheckBox("Lưu file âm thanh WAV (~115 MB/giờ)")
        self.save_audio.setChecked(cfg.save_audio)
        self.silence = QDoubleSpinBox()
        self.silence.setRange(0.3, 2.0)
        self.silence.setSingleStep(0.1)
        self.silence.setValue(cfg.silence_to_final_s)
        self.silence.setSuffix(" s")
        self.maxseg = QDoubleSpinBox()
        self.maxseg.setRange(4, 30)
        self.maxseg.setValue(cfg.max_segment_s)
        self.maxseg.setSuffix(" s")
        f.addRow("Nguồn mặc định:", self.source)
        f.addRow("Model ASR:", self.asr_model)
        f.addRow("Thiết bị ASR:", self.asr_device)
        f.addRow("Nhịp dịch:", self.translation_mode)
        f.addRow("Chế độ:", self.mode)
        f.addRow("", self.save_audio)
        f.addRow("Im lặng để chốt câu:", self.silence)
        f.addRow("Độ dài câu tối đa:", self.maxseg)
        devs = list_devices()
        f.addRow(muted("Thiết bị tìm thấy: " + (", ".join(f"{d.name} ({'loopback' if d.kind == 'loopback' else 'mic'})"
                                                          for d in devs) or "không có (chưa cài soundcard?)")))
        f.addRow(muted("Ngôn ngữ được tự nhận diện theo từng đoạn. Nhãn người nói là ước lượng nhẹ từ âm sắc; "
                       "có thể nhầm và không phải nhận diện danh tính."))
        f.addRow(muted("Chọn model theo kết quả benchmark (tools/benchmark_asr.py). "
                       "Model lớn chính xác hơn nhưng có thể không theo kịp realtime trên CPU."))
        tabs.addTab(a, "Âm thanh & ASR")

        # ---------------- API
        p = QWidget()
        pf = QFormLayout(p)
        self.tr_enabled = QCheckBox("Bật dịch bằng AI (gửi văn bản qua Internet)")
        self.tr_enabled.setChecked(cfg.translation_enabled)
        self.auto_summary = QCheckBox("Tự tạo tóm tắt tiếng Việt khi kết thúc buổi (gửi transcript qua API)")
        self.auto_summary.setChecked(cfg.auto_summary_on_finish)
        self.base_url = QLineEdit(cfg.api_base_url)
        self.model = QLineEdit(cfg.api_model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText(f"Hiện tại: {secrets.mask(secrets.get_api_key())} — nhập để thay")
        key_row = QHBoxLayout()
        save_key = QPushButton("Lưu key")
        save_key.clicked.connect(self._save_key)
        del_key = QPushButton("Xóa key")
        del_key.clicked.connect(self._del_key)
        test = QPushButton("Thử kết nối")
        test.clicked.connect(self._test)
        key_row.addWidget(self.key, 1)
        key_row.addWidget(save_key)
        key_row.addWidget(del_key)
        key_row.addWidget(test)
        self.test_lbl = muted("")
        self.stream = QCheckBox("Streaming (đo TTFT)")
        self.stream.setChecked(cfg.api_stream)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(2, 60)
        self.timeout.setValue(cfg.api_timeout_s)
        self.timeout.setSuffix(" s")
        self.extra = QPlainTextEdit(json.dumps(cfg.api_extra_body, ensure_ascii=False))
        self.extra.setMaximumHeight(60)
        self.extra.setPlaceholderText('{} — tham số riêng của provider, vd. tắt suy luận sâu nếu docs có hỗ trợ')
        self.price_in = QDoubleSpinBox()
        self.price_in.setRange(0, 1000)
        self.price_in.setDecimals(4)
        self.price_in.setValue(cfg.price_in_per_mtok)
        self.price_out = QDoubleSpinBox()
        self.price_out.setRange(0, 1000)
        self.price_out.setDecimals(4)
        self.price_out.setValue(cfg.price_out_per_mtok)
        self.budget = QDoubleSpinBox()
        self.budget.setRange(0, 10000)
        self.budget.setValue(cfg.monthly_budget_usd)
        self.budget.setSuffix(" USD/tháng (0 = không giới hạn)")
        self.ctx = QSpinBox()
        self.ctx.setRange(0, 4)
        self.ctx.setValue(cfg.context_sentences)
        pf.addRow("", self.tr_enabled)
        pf.addRow("", self.auto_summary)
        pf.addRow("Base URL:", self.base_url)
        pf.addRow("Model:", self.model)
        pf.addRow("API key:", key_row)
        pf.addRow("", self.test_lbl)
        pf.addRow("", self.stream)
        pf.addRow("Timeout:", self.timeout)
        pf.addRow("Tham số thêm (JSON):", self.extra)
        pf.addRow("Giá input (USD/1M token):", self.price_in)
        pf.addRow("Giá output (USD/1M token):", self.price_out)
        pf.addRow("Giới hạn chi phí:", self.budget)
        pf.addRow("Số câu ngữ cảnh:", self.ctx)
        pf.addRow(muted(f"Chi phí tháng này (ước tính theo giá bạn nhập): ${repo.month_cost_usd():.4f}. "
                        "Base URL mặc định lấy từ catalog AgnesAI-Labs — hãy đối chiếu tài liệu/console của tài khoản bạn."))
        tabs.addTab(p, "API dịch")

        tabs.addTab(GlossaryEditor(repo), "Thuật ngữ")

        # ---------------- Giao diện
        u = QWidget()
        uf = QFormLayout(u)
        self.theme = QComboBox()
        self.theme.addItem("Tối", "dark")
        self.theme.addItem("Sáng", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(cfg.theme)))
        self.font_px = QSpinBox()
        self.font_px.setRange(14, 32)
        self.font_px.setValue(cfg.subtitle_font_px)
        self.font_px.setSuffix(" px")
        self.autoscroll = QCheckBox("Tự cuộn phụ đề")
        self.autoscroll.setChecked(cfg.auto_scroll)
        uf.addRow("Giao diện:", self.theme)
        uf.addRow("Cỡ chữ phụ đề:", self.font_px)
        uf.addRow("", self.autoscroll)
        tabs.addTab(u, "Giao diện")

        pr = QWidget()
        pl = QVBoxLayout(pr)
        lb = QLabel(PRIVACY_TEXT)
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pl.addWidget(lb)
        pl.addStretch(1)
        tabs.addTab(pr, "Quyền riêng tư")

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Lưu")
        bb.button(QDialogButtonBox.Cancel).setText("Hủy")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.glossary_editor = tabs.widget(2)

    def _save_key(self) -> None:
        v = self.key.text().strip()
        if not v:
            return
        if secrets.set_api_key(v):
            self.key.clear()
            self.key.setPlaceholderText(f"Hiện tại: {secrets.mask(v)}")
            self.test_lbl.setText("Đã lưu key vào Windows Credential Manager.")
        else:
            QMessageBox.warning(self, "Lỗi", "Không lưu được key (keyring không khả dụng).")

    def _del_key(self) -> None:
        secrets.delete_api_key()
        self.key.setPlaceholderText("Hiện tại: (chưa có)")
        self.test_lbl.setText("Đã xóa key.")

    def _test(self) -> None:
        key = self.key.text().strip() or secrets.get_api_key()
        if not key:
            self.test_lbl.setText("Chưa có API key.")
            return
        base, model, stream = self.base_url.text().strip(), self.model.text().strip(), self.stream.isChecked()
        try:
            extra = json.loads(self.extra.toPlainText() or "{}")
        except json.JSONDecodeError:
            self.test_lbl.setText("JSON tham số thêm không hợp lệ.")
            return
        self.test_lbl.setText("Đang thử…")

        def job(_):
            a = AgnesAdapter(base, key, model, stream, extra)
            t0 = time.perf_counter()
            try:
                r = a.complete([{"role": "user", "content": "Translate to Vietnamese: Good morning, class."}],
                               max_tokens=40, timeout_s=15)
                return f"✓ OK · model {r.model} · TTFT {r.ttft_ms} ms · tổng {r.total_ms} ms · “{r.text[:60]}”"
            except LLMError as e:
                return f"✕ {e.code}: {e} ({int((time.perf_counter() - t0) * 1000)} ms)"
            finally:
                a.close()

        run_async(job, self.test_lbl.setText, lambda m: self.test_lbl.setText("✕ " + m))

    def _accept(self) -> None:
        try:
            extra = json.loads(self.extra.toPlainText() or "{}")
            if not isinstance(extra, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            QMessageBox.warning(self, "Lỗi", "Tham số thêm phải là JSON object, ví dụ {}")
            return
        c = self.cfg
        c.source_type = self.source.currentData()
        c.asr_model = self.asr_model.currentText()
        c.asr_device = self.asr_device.currentText()
        c.translation_mode = self.translation_mode.currentData()
        c.auto_summary_on_finish = self.auto_summary.isChecked()
        c.asr_mode = self.mode.currentData()
        c.save_audio = self.save_audio.isChecked()
        c.silence_to_final_s = self.silence.value()
        c.max_segment_s = self.maxseg.value()
        c.translation_enabled = self.tr_enabled.isChecked()
        c.api_base_url = self.base_url.text().strip()
        c.api_model = self.model.text().strip()
        c.api_stream = self.stream.isChecked()
        c.api_timeout_s = self.timeout.value()
        c.api_extra_body = extra
        c.price_in_per_mtok = self.price_in.value()
        c.price_out_per_mtok = self.price_out.value()
        c.monthly_budget_usd = self.budget.value()
        c.context_sentences = self.ctx.value()
        c.theme = self.theme.currentData()
        c.subtitle_font_px = self.font_px.value()
        c.auto_scroll = self.autoscroll.isChecked()
        if self.key.text().strip():
            self._save_key()
        self.glossary_editor.save() if self.glossary_editor.course.currentData() else None
        c.save()
        self.accept()
