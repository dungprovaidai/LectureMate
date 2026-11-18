"""UI smoke + SubtitleWidget + thư viện + seek (pytest-qt, offscreen)."""
import time

import pytest

pytest.importorskip("PySide6")

from conftest import make_final  # noqa: E402
from lecturemate.ai.llm_provider import MockProvider  # noqa: E402
from lecturemate.asr.transcriber import FakeTranscriber  # noqa: E402
from lecturemate.audio.sources import FakeAudioSource  # noqa: E402
from lecturemate.config import AppConfig  # noqa: E402
from lecturemate.domain.models import SessionState, TranscriptEvent, TranslationResult, TranslationStatus  # noqa: E402
from lecturemate.ui.audio_player import segment_at  # noqa: E402
from lecturemate.ui.subtitle_widget import FAILED_TEXT, PENDING_TEXT, SubtitleView  # noqa: E402


def ev(t, sid, v=1, text="hello", start=0):
    return TranscriptEvent(t, "S", sid, start, start + 900, text, v)


# ------------------------------------------------------------ subtitles
def test_partial_updates_same_row(qtbot):
    v = SubtitleView()
    qtbot.addWidget(v)
    v.apply_event(ev("partial", "a", 1, "hel"))
    v.apply_event(ev("partial", "a", 2, "hello wor"))
    assert len(v.rows) == 1 and v.rows["a"].en.text() == "hello wor"
    v.apply_event(ev("partial", "a", 1, "stale"))  # version cũ bị bỏ
    assert v.rows["a"].en.text() == "hello wor"


def test_final_is_stable(qtbot):
    v = SubtitleView()
    qtbot.addWidget(v)
    v.apply_event(ev("partial", "a", 1, "hel"))
    v.apply_event(ev("final", "a", 3, "hello world"))
    v.apply_event(ev("partial", "a", 9, "garbage"))
    v.apply_event(ev("discard", "a"))
    r = v.rows["a"]
    assert r.is_final and r.en.text() == "hello world" and len(v.rows) == 1
    assert r.vi.text() == PENDING_TEXT and r.state_lbl.text() == "Đã lưu"


def test_discard_removes_partial_only(qtbot):
    v = SubtitleView()
    qtbot.addWidget(v)
    v.apply_event(ev("partial", "a", 1))
    v.apply_event(ev("discard", "a"))
    assert v.rows == {}


def test_translation_states(qtbot):
    v = SubtitleView()
    qtbot.addWidget(v)
    v.apply_event(ev("final", "a", 1, "hi"))
    v.apply_translation("a", TranslationResult("a", None, TranslationStatus.FAILED, error_code="auth"))
    assert FAILED_TEXT in v.rows["a"].vi.text() and not v.rows["a"].retry_btn.isHidden()
    v.apply_translation("a", TranslationResult("a", "xin chào", TranslationStatus.DONE))
    assert v.rows["a"].vi.text() == "xin chào" and v.rows["a"].retry_btn.isHidden()


def test_live_preview_translation_updates_the_large_line(qtbot):
    from PySide6.QtWidgets import QVBoxLayout

    from lecturemate.ui.subtitle_widget import SubtitleRow

    row = SubtitleRow("s")
    qtbot.addWidget(row)
    row.set_partial(TranscriptEvent("partial", "S", "s", 0, 900, "hello there", 1,
                                    language="en", speaker_id="1"))
    row.set_preview_translation("Chào bạn", 0)

    assert isinstance(row.body, QVBoxLayout)
    assert row.body.itemAt(1).widget() is row.en
    assert row.vi.text() == "Chào bạn"
    assert row.speaker_lbl.text().endswith("Người nói 1 · ước lượng")


def test_many_rows_and_find(qtbot):
    v = SubtitleView()
    qtbot.addWidget(v)
    for i in range(200):
        v.apply_event(ev("final", f"s{i}", 1, f"sentence {i} inflation" if i % 50 == 0 else f"sentence {i}", i * 1000))
    assert v.final_count() == 200
    assert v.find("inflation") == 4


def test_narrow_layout(qtbot):
    from PySide6.QtWidgets import QBoxLayout

    v = SubtitleView()
    qtbot.addWidget(v)
    v.apply_event(ev("final", "a"))
    v.resize(500, 400)
    v.show()
    qtbot.waitUntil(lambda: v._narrow)
    assert v.rows["a"].body.direction() == QBoxLayout.TopToBottom


# ------------------------------------------------------------ main window
@pytest.fixture()
def window(qtbot, tmp_path, monkeypatch, repo):
    monkeypatch.setenv("LECTUREMATE_HOME", str(tmp_path / "home"))
    from lecturemate.ui import main_window as mw

    pattern, words, t = [], [], 0.0
    for i in range(200):
        pattern += [(1.0, 0.3), (0.7, 0.0)]
        words.append((int(t * 1000) + 100, int(t * 1000) + 900, f"word{i}"))
        t += 1.7
    provider = MockProvider(lambda m: "bản dịch", latency_s=0.01)
    cfg = AppConfig(privacy_accepted=True, partial_interval_s=0.3, auto_summary_on_finish=False)
    cfg.save = lambda *a, **k: None
    w = mw.MainWindow(cfg, repo, transcriber_factory=lambda: FakeTranscriber(words),
                      provider_getter=lambda: provider,
                      source_factory=lambda kind, dev: FakeAudioSource(pattern, realtime=True))
    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(lambda *a, **k: mw.QMessageBox.Yes))
    qtbot.addWidget(w)
    w.show()
    yield w
    w.close()


def test_start_pause_stop_ui_responsive(qtbot, window, repo):
    w = window
    p = w.page
    assert p.start_btn.isEnabled() and not p.stop_btn.isEnabled()
    qtbot.mouseClick(p.start_btn, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.LeftButton)
    qtbot.waitUntil(lambda: w.live and w.live.state == SessionState.RECORDING, timeout=5000)
    qtbot.waitUntil(lambda: p.rec_lbl.text() == "● ĐANG GHI", timeout=2000)  # signal về UI thread
    assert p.rec_lbl.text() == "● ĐANG GHI" and p.stop_btn.isEnabled() and not p.start_btn.isEnabled()
    qtbot.waitUntil(lambda: p.subs.final_count() >= 2, timeout=8000)
    # UI vẫn phản hồi < 200ms khi worker đang chạy
    t0 = time.perf_counter()
    w.toggle_pause()
    qtbot.waitUntil(lambda: p.pause_btn.text().startswith("▶"), timeout=1000)
    assert (time.perf_counter() - t0) < 0.2 + 0.05
    w.toggle_pause()
    qtbot.waitUntil(lambda: w.live.state == SessionState.RECORDING, timeout=1000)
    qtbot.waitUntil(lambda: any(r.vi.text() == "bản dịch" for r in p.subs.rows.values()), timeout=5000)
    w.confirm_stop()
    qtbot.waitUntil(lambda: w.stack.currentWidget() is w.detail, timeout=15000)
    sid = w.live.session_id
    assert repo.get_session(sid)["status"] == "completed"
    assert w.detail.table.rowCount() == len(repo.list_segments(sid)) >= 2
    assert p.start_btn.isEnabled()


def test_library_lists_and_deletes(qtbot, window, repo, monkeypatch):
    from lecturemate.ui import session_library as sl

    a = repo.create_session("Bài A", "microphone")
    b = repo.create_session("Bài B", "microphone")
    for i in range(3):
        repo.commit_final(make_final(a, i, f"a{i}"))
    repo.update_status(a, SessionState.COMPLETED)
    window.show_library()
    lib = window.library
    assert lib.table.rowCount() == 2
    lib.search.setText("Bài A")
    assert lib.table.rowCount() == 1
    lib.table.selectRow(0)
    monkeypatch.setattr(sl.QMessageBox, "exec", lambda self: sl.QMessageBox.Yes)
    lib._delete()
    assert repo.get_session(a) is None and repo.get_session(b) is not None


def test_detail_seek_and_missing_audio(qtbot, window, repo):
    sid = repo.create_session("Không audio", "microphone")
    for i in range(5):
        repo.commit_final(make_final(sid, i, f"line {i}", start=i * 10_000, end=i * 10_000 + 8000))
    repo.update_status(sid, SessionState.COMPLETED)
    window.open_detail(sid)
    d = window.detail
    assert d.table.rowCount() == 5
    assert not d.player.available and "Chưa có file âm thanh" in d.player.msg.text()
    d._on_cell_clicked(2, 1)  # không crash khi thiếu file
    d._highlight_at(21_000)
    assert d.table.currentRow() == 2


def test_segment_at_accuracy():
    class S:
        def __init__(self, a, b):
            self.start_ms, self.end_ms = a, b

    segs = [S(i * 2000, i * 2000 + 1800) for i in range(100)]
    for ms in range(0, 200_000, 137):
        s = segment_at(segs, ms)
        assert s is not None and s.start_ms <= ms and ms - s.start_ms < 2000
    assert segment_at(segs, -5) is None


def test_settings_dialog_opens(qtbot, window, repo):
    from lecturemate.ui.settings_dialog import SettingsDialog

    d = SettingsDialog(window.cfg, repo)
    qtbot.addWidget(d)
    d.theme.setCurrentIndex(1)
    d.translation_mode.setCurrentIndex(d.translation_mode.findData("fast"))
    d.auto_summary.setChecked(True)
    d._accept()
    assert window.cfg.theme == "light"
    assert window.cfg.translation_mode == "fast" and window.cfg.auto_summary_on_finish
