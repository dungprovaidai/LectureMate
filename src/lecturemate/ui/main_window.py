"""Cửa sổ chính: Bảng điều khiển trực tiếp · Thư viện · Chi tiết · Thiết lập."""
from __future__ import annotations

import logging
import os
import threading
from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..ai import secrets
from ..ai.agnes_adapter import AgnesAdapter
from ..app_controller import AppServices, LiveSession, ensure_course
from ..audio import SAMPLE_RATE
from ..audio.audio_worker import rms_dbfs
from ..audio.sources import EndOfStream, FileAudioSource, SoundCardSource, list_devices
from ..config import AppConfig, audio_dir
from ..domain.models import SessionState, TranscriptEvent, TranslationResult, TranslationStatus, format_ts
from ..metrics import percentile
from ..persistence.repository import SessionRepository
from .bridge import Bridge, run_async
from .session_detail import SessionDetail
from .session_library import SessionLibrary
from .settings_dialog import PRIVACY_TEXT, SettingsDialog
from .subtitle_widget import SubtitleView
from .theme import stylesheet
from .widgets import APIStatus, ErrorBanner, LevelMeter, muted

log = logging.getLogger(__name__)


class ProviderCache:
    """Tạo lại AgnesAdapter khi key/base URL/model đổi. Không giữ key ở chỗ khác."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._sig = None
        self._p = None
        self._lock = threading.Lock()

    def get(self):
        key = secrets.get_api_key()
        if not key or not self.cfg.api_base_url:
            return None
        sig = (hash(key), self.cfg.api_base_url, self.cfg.api_model, self.cfg.api_stream,
               repr(sorted(self.cfg.api_extra_body.items())))
        with self._lock:
            if sig != self._sig:
                if self._p:
                    self._p.close()
                self._p = AgnesAdapter(self.cfg.api_base_url, key, self.cfg.api_model, self.cfg.api_stream,
                                       self.cfg.api_extra_body)
                self._sig = sig
            return self._p


class TranscriberCache:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._key = None
        self._tr = None

    def get(self):
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            return None
        from ..asr.transcriber import make_transcriber

        key = (self.cfg.asr_model, self.cfg.asr_device, self.cfg.asr_compute_type, self.cfg.language)
        if key != self._key:
            self._tr = make_transcriber(self.cfg)
            self._key = key
        return self._tr


class PrivacyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trước khi bắt đầu")
        lay = QVBoxLayout(self)
        h = QLabel("Dữ liệu của bạn ở đâu?")
        h.setObjectName("h1")
        lay.addWidget(h)
        t = QLabel(PRIVACY_TEXT)
        t.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(muted("Bắt đầu: 1) chọn nguồn âm thanh → 2) “Thử 5 giây” xem thanh mức âm → "
                            "3) nhập API key trong Thiết lập (không bắt buộc: chưa có key vẫn ghi âm và phiên âm)."))
        self.ok = QCheckBox("Tôi hiểu và chỉ ghi âm khi được phép")
        lay.addWidget(self.ok)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Đồng ý")
        bb.button(QDialogButtonBox.Cancel).setText("Thoát")
        bb.button(QDialogButtonBox.Ok).setEnabled(False)
        self.ok.toggled.connect(bb.button(QDialogButtonBox.Ok).setEnabled)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class LivePage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        top = QFrame()
        top.setObjectName("panel")
        tl = QHBoxLayout(top)
        brand = QLabel("LectureMate")
        brand.setObjectName("h1")
        self.title = QLineEdit()
        self.title.setAccessibleName("Tên buổi học")
        self.title.setPlaceholderText("Tên buổi học")
        self.course = QComboBox()
        self.course.setEditable(True)
        self.course.setAccessibleName("Môn học")
        self.course.lineEdit().setPlaceholderText("Môn học (tùy chọn)")
        self.course.setMinimumWidth(160)
        self.source = QComboBox()
        self.source.setAccessibleName("Nguồn âm thanh")
        self.source.addItem("🎤 Microphone", "microphone")
        self.source.addItem("🔊 System Audio", "loopback")
        self.device = QComboBox()
        self.device.setAccessibleName("Thiết bị")
        self.device.setMinimumWidth(180)
        self.test_btn = QPushButton("Thử 5 giây")
        self.test_btn.setToolTip("Đo mức âm đầu vào trong 5 giây, không lưu bản ghi")
        for w in (brand, self.title, self.course, self.source, self.device, self.test_btn):
            tl.addWidget(w, 1 if w is self.title else 0)
        lay.addWidget(top)

        self.banner = ErrorBanner()
        lay.addWidget(self.banner)

        heads = QHBoxLayout()
        a = QLabel("Bản dịch tiếng Việt ở trên · nguyên văn nhỏ bên dưới · nhãn giọng/ngôn ngữ là ước lượng")
        a.setObjectName("muted")
        heads.addWidget(a, 1)
        lay.addLayout(heads)
        self.subs = SubtitleView()
        lay.addWidget(self.subs, 1)

        self.find_bar = QFrame()
        fb = QHBoxLayout(self.find_bar)
        fb.setContentsMargins(0, 0, 0, 0)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Tìm trong phiên… (Enter: kết quả kế tiếp, Esc: đóng)")
        self.find_count = muted("")
        fb.addWidget(self.find_edit, 1)
        fb.addWidget(self.find_count)
        self.find_bar.hide()
        lay.addWidget(self.find_bar)

        bottom = QFrame()
        bottom.setObjectName("panel")
        bl = QHBoxLayout(bottom)
        self.start_btn = QPushButton("● Bắt đầu  (Ctrl+R)")
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumHeight(40)
        self.pause_btn = QPushButton("⏸ Tạm dừng  (Ctrl+P)")
        self.pause_btn.setMinimumHeight(40)
        self.stop_btn = QPushButton("■ Kết thúc")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setMinimumHeight(40)
        self.rec_lbl = QLabel("")
        self.rec_lbl.setObjectName("badge_err")
        self.clock = QLabel("00:00:00")
        self.clock.setObjectName("clock")
        self.clock.setAccessibleName("Thời gian ghi")
        self.meter = LevelMeter()
        self.meter_lbl = QLabel("Mức âm")
        self.meter_lbl.setObjectName("muted")
        self.api = APIStatus()
        self.p50 = muted("Trễ p50: —")
        self.autoscroll = QCheckBox("Tự cuộn")
        for w in (self.start_btn, self.pause_btn, self.stop_btn, self.rec_lbl, self.clock, self.meter_lbl,
                  self.meter, self.api, self.p50):
            bl.addWidget(w)
        bl.addStretch(1)
        bl.addWidget(self.autoscroll)
        lay.addWidget(bottom)
        self.privacy = muted("Dịch bằng AI sẽ gửi các đoạn văn bản qua Internet. Âm thanh chỉ lưu trên máy.")
        lay.addWidget(self.privacy)


class MainWindow(QMainWindow):
    def __init__(self, cfg: AppConfig, repo: SessionRepository, transcriber_factory=None, provider_getter=None,
                 source_factory=None):
        super().__init__()
        self.cfg = cfg
        self.repo = repo
        self.setWindowTitle(f"LectureMate AI {__version__}")
        self.resize(1180, 760)
        self.setMinimumSize(960, 620)
        self.bridge = Bridge(self)
        self._providers = ProviderCache(cfg)
        self.provider_getter = provider_getter or self._providers.get
        self._transcribers = TranscriberCache(cfg)
        self.transcriber_factory = transcriber_factory or self._transcribers.get
        self.source_factory = source_factory or self._default_source
        self.sv = AppServices(cfg, repo, self.provider_getter, self.bridge.callbacks())
        self.live: LiveSession | None = None
        self._testing_device = False
        self._recent_totals: deque[int] = deque(maxlen=200)
        self._pending_translations: dict[str, TranslationResult] = {}

        central = QWidget()
        central.setObjectName("page")
        cl = QVBoxLayout(central)
        cl.setContentsMargins(12, 8, 12, 8)
        nav = QHBoxLayout()
        self.nav_live = QPushButton("Bảng điều khiển")
        self.nav_lib = QPushButton("Thư viện (Ctrl+L)")
        self.nav_set = QPushButton("Thiết lập")
        for b in (self.nav_live, self.nav_lib, self.nav_set):
            nav.addWidget(b)
        nav.addStretch(1)
        cl.addLayout(nav)
        self.stack = QStackedWidget()
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.page = LivePage(self)
        self.library = SessionLibrary(repo)
        self.detail = SessionDetail(self.sv, self.provider_getter, self.transcriber_factory)
        for w in (self.page, self.library, self.detail):
            self.stack.addWidget(w)

        p = self.page
        p.autoscroll.setChecked(cfg.auto_scroll)
        p.subs.auto_scroll = cfg.auto_scroll
        p.autoscroll.toggled.connect(lambda v: setattr(p.subs, "auto_scroll", v))
        p.source.setCurrentIndex(max(0, p.source.findData(cfg.source_type)))
        p.source.currentIndexChanged.connect(self._reload_devices)
        p.test_btn.clicked.connect(self._test_device)
        p.start_btn.clicked.connect(self.start_session)
        p.pause_btn.clicked.connect(self.toggle_pause)
        p.stop_btn.clicked.connect(self.confirm_stop)
        p.subs.retry_requested.connect(self._retry_one)
        p.find_edit.textChanged.connect(lambda t: p.find_count.setText(f"{p.subs.find(t)} kết quả" if t else ""))
        p.find_edit.returnPressed.connect(p.subs.find_next)
        self.nav_live.clicked.connect(lambda: self.stack.setCurrentWidget(self.page))
        self.nav_lib.clicked.connect(self.show_library)
        self.nav_set.clicked.connect(self.open_settings)
        self.library.open_session.connect(self.open_detail)
        self.library.new_session.connect(lambda: self.stack.setCurrentWidget(self.page))
        self.detail.back.connect(self.show_library)

        b = self.bridge
        b.transcript.connect(self._on_transcript)
        b.translation.connect(self._on_translation)
        b.level.connect(p.meter.set_db)
        b.state.connect(self._on_state)
        b.error.connect(lambda m: p.banner.show_message(m, "error"))
        b.info.connect(lambda m: p.banner.show_message(m, "info", 8000))
        b.api_status.connect(self._on_api_status)

        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._ctrl_r)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.toggle_pause)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._ctrl_f)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.show_library)
        QShortcut(QKeySequence("Escape"), p.find_edit, activated=lambda: (p.find_bar.hide(), p.subs.find("")))
        quit_act = QAction("Thoát", self, shortcut=QKeySequence.Quit, triggered=self.close)
        self.addAction(quit_act)

        self._clock = QTimer(self, interval=250, timeout=self._tick)
        self._ckpt = QTimer(self, interval=5000, timeout=lambda: self.live and self.live.checkpoint())
        self.apply_theme()
        self._reload_courses()
        self._reload_devices()
        self._update_controls(SessionState.IDLE)
        self._new_title()
        p.api.set_status("idle" if self.provider_getter() else "disabled")
        self.statusBar().showMessage(f"Dữ liệu: {audio_dir().parent}")

    # --------------------------------------------------------------- setup
    def apply_theme(self) -> None:
        QApplication.instance().setStyleSheet(stylesheet(self.cfg.theme, self.cfg.subtitle_font_px))

    def _new_title(self) -> None:
        self.page.title.setText(f"Buổi học {datetime.now():%d/%m %H:%M}")

    def _reload_courses(self) -> None:
        cur = self.page.course.currentText()
        self.page.course.clear()
        self.page.course.addItem("")
        for c in self.repo.list_courses():
            self.page.course.addItem(c["name"], c["id"])
        self.page.course.setCurrentText(cur)
        self.library.reload_courses()

    def _reload_devices(self) -> None:
        kind = self.page.source.currentData()
        self.page.device.clear()
        default_label = "Mặc định (ưu tiên micro thật)" if kind == "microphone" else "Mặc định"
        self.page.device.addItem(default_label, "")
        try:
            for d in list_devices():
                if d.kind == kind:
                    self.page.device.addItem(d.name + (" (mặc định)" if d.is_default else ""), d.id)
        except Exception as exc:
            self.page.banner.show_message(str(exc))
        i = self.page.device.findData(self.cfg.device_id)
        self.page.device.setCurrentIndex(max(0, i))

    def _default_source(self, kind: str, device_id: str):
        demo = os.environ.get("LECTUREMATE_DEMO_WAV")
        if demo:  # chạy demo/kiểm thử không cần micro
            return FileAudioSource(demo, realtime=True)
        return SoundCardSource(kind, device_id)

    # ------------------------------------------------------------ actions
    def _ctrl_r(self) -> None:
        if self.live and self.live.state in (SessionState.RECORDING, SessionState.PAUSED):
            self.confirm_stop()
        else:
            self.start_session()

    def _ctrl_f(self) -> None:
        if self.stack.currentWidget() is self.detail:
            self.detail.find_edit.setFocus()
            return
        self.page.find_bar.show()
        self.page.find_edit.setFocus()
        self.page.find_edit.selectAll()

    def start_session(self) -> None:
        if not self.page.start_btn.isEnabled():
            return
        if self.live and self.live.state not in (SessionState.COMPLETED, SessionState.IDLE, SessionState.ERROR):
            return
        p = self.page
        self.cfg.source_type = p.source.currentData()
        self.cfg.device_id = p.device.currentData() or ""
        self.cfg.save()
        transcriber = None if self.cfg.asr_mode == "record_only" else self.transcriber_factory()
        if transcriber is None and self.cfg.asr_mode != "record_only":
            if QMessageBox.question(self, "Chưa có ASR",
                                    "Chưa cài faster-whisper nên không có phụ đề trực tiếp.\n"
                                    "Ghi âm ở chế độ “chỉ ghi âm” và phiên âm sau?") != QMessageBox.Yes:
                return
        course_id = ensure_course(self.repo, p.course.currentText())
        self._reload_courses()
        p.subs.clear()
        p.subs.translating = self.cfg.translation_enabled and self.provider_getter() is not None
        p.banner.hide()
        self._recent_totals.clear()
        p.p50.setText("Trễ p50: —")
        src = self.source_factory(self.cfg.source_type, self.cfg.device_id)
        self.live = LiveSession(self.sv, src, transcriber, audio_dir())
        self._update_controls(SessionState.CHECKING_DEVICE)
        live = self.live
        title = p.title.text().strip() or "Buổi học"

        def job(_):
            live.start(title, course_id)
            return live.session_id

        def ok(sid):
            self._clock.start()
            self._ckpt.start()
            if live.record_only:
                p.banner.show_message("Chế độ chỉ ghi âm: phụ đề sẽ có sau khi phiên âm từ file.", "info", 8000)

        def fail(msg):
            p.banner.show_message(f"Không bắt đầu được: {msg}. Kiểm tra quyền micro (Settings → Privacy → "
                                  "Microphone) hoặc chọn thiết bị khác.")
            self._update_controls(SessionState.IDLE)

        run_async(job, ok, fail)

    def toggle_pause(self) -> None:
        if not self.live:
            return
        if self.live.state == SessionState.RECORDING:
            self.live.pause()
        elif self.live.state == SessionState.PAUSED:
            self.live.resume()

    def confirm_stop(self) -> None:
        if not self.live or self.live.state not in (SessionState.RECORDING, SessionState.PAUSED):
            return
        if QMessageBox.question(self, "Kết thúc buổi học", "Kết thúc và lưu buổi học?") != QMessageBox.Yes:
            return
        self.stop_session()

    def stop_session(self, then=None) -> None:
        live = self.live
        if not live:
            return
        self.page.stop_btn.setEnabled(False)
        self.page.stop_btn.setText("Đang lưu…")

        def done(_):
            self._clock.stop()
            self._ckpt.stop()
            sid = live.session_id
            self.page.stop_btn.setText("■ Kết thúc")
            self._update_controls(SessionState.COMPLETED)
            self._new_title()
            if then:
                then()
            elif sid:
                counts = self.repo.translation_counts(sid)
                missing = sum(v for k, v in counts.items() if k != "done")
                msg = "Đã lưu buổi học."
                if missing:
                    msg += f" {missing} đoạn chưa dịch — có thể dịch lại trong trang chi tiết."
                self.page.banner.show_message(msg, "info", 10000)
                self.open_detail(sid)
                if self.cfg.auto_summary_on_finish:
                    self.detail.generate_summary_after_session()

        run_async(lambda _: live.stop(), done, lambda m: self.page.banner.show_message("Lỗi khi dừng: " + m))

    def _retry_one(self, segment_id: str) -> None:
        seg = self.repo.get_segment(segment_id)
        if seg and seg.translation_status in ("failed", "pending"):
            self.repo.release_translation(segment_id, TranslationStatus.PENDING, None)
            self.sv.tq.submit(segment_id, seg.session_id)
            row = self.page.subs.rows.get(segment_id)
            if row:
                row.set_translation(None, "pending")

    def _test_device(self) -> None:
        p = self.page
        if not p.test_btn.isEnabled():
            return
        kind = p.source.currentData()
        source = self.source_factory(kind, p.device.currentData() or "")
        quiet_blocks = 10 if kind == "microphone" else 0  # 1 giây đo nền cho micro
        state = self.live.state if self.live else SessionState.IDLE
        self._testing_device = True
        self._update_controls(state)
        p.banner.show_message("Giữ im lặng 1 giây để đo tiếng nền…" if quiet_blocks else
                              "Hãy phát video hoặc bài giảng để thử âm thanh máy…", "info")

        def job(progress):
            levels = []
            try:
                source.open()
                for i in range(50):
                    if quiet_blocks and i == quiet_blocks:
                        progress(i / 50)
                    db = rms_dbfs(source.read(SAMPLE_RATE // 10))
                    levels.append(db)
                    self.bridge.level.emit(db)
            except EndOfStream:
                pass
            finally:
                source.close()
            return levels

        def reset_controls():
            self._testing_device = False
            self._update_controls(state)

        def done(levels):
            reset_controls()
            # Bỏ qua đỉnh đơn lẻ (ví dụ gõ bàn) và so với mức nền đã đo.
            signal = percentile(levels[quiet_blocks:], 90)
            floor = percentile(levels[:quiet_blocks], 50)
            if signal is None:
                p.banner.show_message("Mẫu thử quá ngắn. Thử lại với thiết bị đang dùng.")
            elif quiet_blocks and signal < -72:
                p.banner.show_message("Gần như không có tín hiệu mic. Chọn Microphone Array (không chọn Stereo Mix), "
                                      "kiểm tra quyền truy cập và mức Đầu vào trong Âm thanh của Windows.")
            elif floor is not None and signal - floor < 7:
                p.banner.show_message("Chưa thấy lời nói nổi hơn mức nền. Thử lại: im lúc đầu, rồi nói khi có nhắc; "
                                      "đưa mic gần hơn hoặc giảm tiếng quạt và tiếng nền.")
            elif quiet_blocks and signal < -42:
                tip = ("Hỗ trợ mic yếu đang bật." if self.cfg.mic_assist else
                       "Có thể bật Hỗ trợ mic yếu trong Thiết lập → Âm thanh & ASR.")
                p.banner.show_message(f"Có tín hiệu nhưng mức thu thấp ({signal:.0f} dBFS). {tip} "
                                      "Đưa mic gần hơn để thu rõ hơn.", "info")
            elif not quiet_blocks and signal < -55:
                p.banner.show_message("Chưa thấy tín hiệu âm thanh máy. Kiểm tra loa đầu ra và phát lại video để thử.")
            else:
                p.banner.show_message(f"Mức thu đã đo: {signal:.0f} dBFS. Bạn có thể bắt đầu ghi.", "info", 6000)

        def fail(msg):
            reset_controls()
            p.banner.show_message("Không mở được thiết bị: " + msg)

        run_async(job, done, fail, on_progress=lambda _: p.banner.show_message(
            "Bây giờ nói một câu ở khoảng cách bạn thường dùng…", "info"))

    # ------------------------------------------------------------- events
    def _on_transcript(self, ev: TranscriptEvent) -> None:
        if ev.type == "discard":
            self._pending_translations.pop(ev.segment_id, None)
        if self.live and ev.session_id == self.live.session_id:
            self.page.subs.apply_event(ev)
            pending = self._pending_translations.pop(ev.segment_id, None)
            if pending:
                self.page.subs.apply_translation(ev.segment_id, pending)

    def _on_translation(self, segment_id: str, res: TranslationResult) -> None:
        if segment_id not in self.page.subs.rows and self.live:
            existing = self._pending_translations.get(segment_id)
            existing_is_final = existing is not None and not existing.extra.get("preview")
            incoming_is_final = not res.extra.get("preview")
            newer_preview = (existing is not None and existing.extra.get("preview") and res.extra.get("preview")
                             and int(res.extra.get("revision", -1)) >= int(existing.extra.get("revision", -1)))
            if not existing_is_final and (incoming_is_final or existing is None or newer_preview):
                self._pending_translations[segment_id] = res
        self.page.subs.apply_translation(segment_id, res)
        if self.stack.currentWidget() is self.detail:
            self.detail.on_translation(segment_id, res)
        lat = res.extra.get("latency") if res.extra else None
        if lat and lat.get("total_ms") is not None:
            self._recent_totals.append(lat["total_ms"])
            p50 = percentile(list(self._recent_totals), 50)
            self.page.p50.setText(f"Trễ p50: {p50 / 1000:.1f}s (n={len(self._recent_totals)})")

    def _on_api_status(self, s: str) -> None:
        self.page.api.set_status(s)
        if s == "offline":
            self.page.banner.show_message("Mất mạng: vẫn lưu tiếng Anh; sẽ dịch lại khi có mạng.", "info")

    def _on_state(self, old, new) -> None:
        self._update_controls(new)

    def _update_controls(self, st: SessionState) -> None:
        p = self.page
        rec = st in (SessionState.RECORDING, SessionState.PAUSED)
        busy = self._testing_device or st in (SessionState.CHECKING_DEVICE, SessionState.STOPPING)
        p.start_btn.setEnabled(not rec and not busy)
        p.start_btn.setText("Đang mở thiết bị…" if st == SessionState.CHECKING_DEVICE else "● Bắt đầu  (Ctrl+R)")
        p.pause_btn.setEnabled(rec)
        p.pause_btn.setText("▶ Tiếp tục  (Ctrl+P)" if st == SessionState.PAUSED else "⏸ Tạm dừng  (Ctrl+P)")
        p.stop_btn.setEnabled(rec)
        p.test_btn.setText("Đang thử…" if self._testing_device else "Thử 5 giây")
        for w in (p.source, p.device, p.test_btn, p.course, p.title):
            w.setEnabled(not rec and not busy)
        p.rec_lbl.setText({SessionState.RECORDING: "● ĐANG GHI", SessionState.PAUSED: "❚❚ TẠM DỪNG",
                           SessionState.STOPPING: "Đang lưu…"}.get(st, ""))
        self.nav_set.setEnabled(not rec and not busy)

    def _tick(self) -> None:
        if self.live:
            self.page.clock.setText(format_ts(self.live.elapsed_ms()))

    # -------------------------------------------------------------- pages
    def show_library(self) -> None:
        self.library.reload_courses()
        self.library.refresh()
        self.stack.setCurrentWidget(self.library)

    def open_detail(self, session_id: str) -> None:
        self.detail.load(session_id)
        self.stack.setCurrentWidget(self.detail)

    def open_settings(self) -> None:
        d = SettingsDialog(self.cfg, self.repo, self)
        if d.exec():
            self.apply_theme()
            self.sv.invalidate_glossary()
            tq = self.sv.tq
            tq.timeout_s, tq.max_retries = self.cfg.api_timeout_s, self.cfg.api_max_retries
            tq.price_in, tq.price_out = self.cfg.price_in_per_mtok, self.cfg.price_out_per_mtok
            tq.budget, tq.context_sentences = self.cfg.monthly_budget_usd, self.cfg.context_sentences
            tq.asr_model = self.cfg.asr_model
            self.page.source.setCurrentIndex(max(0, self.page.source.findData(self.cfg.source_type)))
            self.page.api.set_status("idle" if self.provider_getter() else "disabled")
        self._reload_courses()

    # ----------------------------------------------------------- startup
    def first_run(self) -> bool:
        if not self.cfg.privacy_accepted:
            d = PrivacyDialog(self)
            if not d.exec():
                return False
            self.cfg.privacy_accepted = True
            self.cfg.save()
        interrupted = self.sv.startup_maintenance()
        for s in interrupted:
            self.sv.recover_session(s["id"])
        if interrupted:
            names = ", ".join(f"“{s['title']}”" for s in interrupted[:3])
            box = QMessageBox(self)
            box.setWindowTitle("Phục hồi buổi học")
            box.setText(f"Phát hiện {len(interrupted)} buổi ghi dở: {names}.")
            box.setInformativeText("Các đoạn đã lưu được giữ nguyên và buổi học được đóng an toàn. "
                                   "Bản dịch còn thiếu có thể dịch lại trong trang chi tiết.")
            open_btn = box.addButton("Mở buổi học", QMessageBox.AcceptRole)
            box.addButton("Để sau", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is open_btn:
                self.open_detail(interrupted[0]["id"])
        pending = self.repo.pending_translations(include_failed=False)
        if pending and self.provider_getter():
            self.sv.tq.retry_pending()
        return True

    def closeEvent(self, e) -> None:
        if self.live and self.live.state in (SessionState.RECORDING, SessionState.PAUSED):
            if QMessageBox.question(self, "Đang ghi", "Buổi học đang ghi. Kết thúc, lưu và thoát?") != QMessageBox.Yes:
                e.ignore()
                return
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.live.stop()
            finally:
                QApplication.restoreOverrideCursor()
        self.detail.player.stop()
        self.sv.shutdown()
        e.accept()
