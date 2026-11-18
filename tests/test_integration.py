"""End-to-end offline: FakeAudioSource (thời gian thực tăng tốc) + FakeTranscriber + MockProvider."""
import threading
import time

import numpy as np
import pytest

from lecturemate.ai.llm_provider import LLMError, MockProvider
from lecturemate.app_controller import AppServices, Callbacks, LiveSession, transcribe_file_job
from lecturemate.asr.transcriber import FakeTranscriber
from lecturemate.audio.sources import FakeAudioSource
from lecturemate.config import AppConfig
from lecturemate.domain.models import SessionState
from lecturemate.persistence.repository import SessionRepository


def lecture(n, speak=1.2, gap=0.8):
    pattern, words, t = [], [], 0.0
    for i in range(n):
        pattern += [(speak, 0.3), (gap, 0.0)]
        words += [(int(t * 1000) + 100, int(t * 1000) + 500, f"alpha{i}"),
                  (int(t * 1000) + 600, int(t * 1000) + 1000, f"beta{i}")]
        t += speak + gap
    return pattern, words


class SpeedSource(FakeAudioSource):
    """Như FakeAudioSource realtime nhưng nhanh gấp ``speed`` lần."""

    def __init__(self, pattern, speed=20.0, **kw):
        super().__init__(pattern, **kw)
        self.speed = speed

    def read(self, frames):
        out = super().read(frames)
        time.sleep(frames / 16000 / self.speed)
        return out


def services(tmp_path, provider, **cfgkw):
    cfg = AppConfig(**{"partial_interval_s": 0.05, **cfgkw})
    repo = SessionRepository(tmp_path / "db.sqlite", audio_root=tmp_path / "audio")
    (tmp_path / "audio").mkdir(exist_ok=True)
    events = {"transcript": [], "translation": [], "errors": [], "states": []}
    cb = Callbacks(
        on_transcript=events["transcript"].append,
        on_translation=lambda sid, r: events["translation"].append(r),
        on_error=events["errors"].append,
        on_state=lambda a, b: events["states"].append(b),
    )
    sv = AppServices(cfg, repo, lambda: provider, cb)
    return sv, repo, events


def wait(cond, t=10):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_end_to_end_session_and_restart(tmp_path):
    pattern, words = lecture(30)
    provider = MockProvider(lambda m: "Bản dịch " + m[1]["content"].split("<cau_can_dich>")[1].split("<")[0])
    sv, repo, ev = services(tmp_path, provider)
    ls = LiveSession(sv, SpeedSource(pattern, speed=40), FakeTranscriber(words), tmp_path / "audio")
    ui_thread = threading.current_thread()
    sid = ls.start("Bài kiểm thử", None)
    assert wait(lambda: ls.state == SessionState.COMPLETED, 20)  # nguồn hết → tự dừng an toàn
    assert wait(lambda: len(ev["translation"]) >= 30)
    segs = repo.list_segments(sid)
    assert len(segs) == 30
    assert [s.sequence_number for s in segs] == list(range(30))
    assert all(s.translation_status == "done" for s in segs)
    assert segs[3].en_text == "alpha3 beta3" and segs[3].vi_text == "Bản dịch alpha3 beta3"
    partial_ids = {e.segment_id for e in ev["transcript"] if e.type == "partial"}
    assert partial_ids  # có partial
    assert ev["states"][:2] == [SessionState.CHECKING_DEVICE, SessionState.RECORDING]
    assert ev["states"][-1] == SessionState.COMPLETED
    sess = repo.get_session(sid)
    assert sess["status"] == "completed" and sess["duration_ms"] >= 59_000
    assert threading.current_thread() is ui_thread
    sv.shutdown()
    repo.close()
    # "khởi động lại" ứng dụng: transcript vẫn còn
    repo2 = SessionRepository(tmp_path / "db.sqlite", audio_root=tmp_path / "audio")
    assert len(repo2.list_segments(sid)) == 30
    lat = repo2.latency_rows(sid)
    assert len(lat) == 30 and all(r["total_ms"] is not None for r in lat)


def test_offline_api_does_not_stop_recording(tmp_path):
    pattern, words = lecture(10)
    provider = MockProvider(responder=lambda m: (_ for _ in ()).throw(LLMError("offline", retryable=True)))
    sv, repo, ev = services(tmp_path, provider, api_max_retries=0)
    sv.tq.offline_cooldown = 0.01
    ls = LiveSession(sv, SpeedSource(pattern, speed=40), FakeTranscriber(words), tmp_path / "audio")
    sid = ls.start("Mất mạng")
    assert wait(lambda: ls.state == SessionState.COMPLETED, 20)
    segs = repo.list_segments(sid)
    assert len(segs) == 10  # ASR + lưu tiếng Anh tiếp tục
    assert all(s.translation_status in ("pending", "in_flight") for s in segs)
    # có mạng lại → dịch bù
    provider.responder = lambda m: "ok"
    sv.tq.retry_pending(sid)
    assert wait(lambda: all(s.translation_status == "done" for s in repo.list_segments(sid)))
    sv.shutdown()


def test_pause_resume_and_manual_stop(tmp_path):
    pattern, words = lecture(40)
    sv, repo, ev = services(tmp_path, MockProvider(lambda m: "ok"))
    ls = LiveSession(sv, SpeedSource(pattern, speed=10), FakeTranscriber(words), tmp_path / "audio")
    sid = ls.start("Pause")
    time.sleep(0.3)
    ls.pause()
    assert ls.state == SessionState.PAUSED
    t_paused = ls.elapsed_ms()
    time.sleep(0.3)
    assert ls.elapsed_ms() == t_paused  # tạm dừng không ghi thêm
    ls.resume()
    time.sleep(0.3)
    ls.stop()
    assert ls.state == SessionState.COMPLETED
    assert repo.get_session(sid)["status"] == "completed"
    sv.shutdown()


def test_device_lost_mid_session(tmp_path):
    pattern, words = lecture(20)
    sv, repo, ev = services(tmp_path, MockProvider(lambda m: "ok"))
    src = SpeedSource(pattern, speed=40, fail_after_s=10.0)
    ls = LiveSession(sv, src, FakeTranscriber(words), tmp_path / "audio")
    sid = ls.start("Rút mic")
    assert wait(lambda: ls.state == SessionState.COMPLETED, 20)
    assert ev["errors"] and "Đã dừng an toàn" in ev["errors"][0]
    assert 3 <= len(repo.list_segments(sid)) <= 6
    sv.shutdown()


def test_device_open_failure(tmp_path):
    from lecturemate.audio.sources import FileAudioSource

    sv, repo, ev = services(tmp_path, None)
    ls = LiveSession(sv, FileAudioSource(tmp_path / "nope.wav"), FakeTranscriber([]), tmp_path / "audio")
    with pytest.raises(RuntimeError):
        ls.start("x")
    assert ls.state == SessionState.COMPLETED or ls.state == SessionState.ERROR
    sv.shutdown()


def test_record_only_then_transcribe_after(tmp_path):
    """Fallback P1.3.B: ghi WAV không ASR, phiên âm + dịch sau buổi; chạy lại không nhân đôi."""
    pattern, words = lecture(12)
    provider = MockProvider(lambda m: "ok")
    sv, repo, ev = services(tmp_path, provider, asr_mode="record_only")
    ls = LiveSession(sv, SpeedSource(pattern, speed=40), FakeTranscriber(words), tmp_path / "audio")
    sid = ls.start("Chỉ ghi âm")
    assert wait(lambda: ls.state == SessionState.COMPLETED, 20)
    assert repo.list_segments(sid) == []
    n = transcribe_file_job(sv, sid, FakeTranscriber(words))
    assert n == 12
    n2 = transcribe_file_job(sv, sid, FakeTranscriber(words))
    assert n2 == 0
    assert len(repo.list_segments(sid)) == 12
    assert wait(lambda: all(s.translation_status == "done" for s in repo.list_segments(sid)))
    sv.shutdown()


def test_crash_recovery(tmp_path):
    """Kill giữa buổi (không gọi stop): segment final đã commit còn nguyên, phiên được phục hồi."""
    pattern, words = lecture(40)
    sv, repo, ev = services(tmp_path, MockProvider(lambda m: "ok"))
    ls = LiveSession(sv, SpeedSource(pattern, speed=20), FakeTranscriber(words), tmp_path / "audio")
    sid = ls.start("Crash")
    assert wait(lambda: len(repo.list_segments(sid)) >= 5)
    committed = {s.id for s in repo.list_segments(sid)}
    ls.audio._stop_evt.set()  # "kill": dừng nguồn, không chốt phiên
    ls.asr.abort()
    sv.tq.stop(0)
    # ứng dụng mới khởi động
    repo2 = SessionRepository(tmp_path / "db.sqlite", audio_root=tmp_path / "audio")
    sv2 = AppServices(sv.cfg, repo2, lambda: None)
    interrupted = sv2.startup_maintenance()
    assert sid in [s["id"] for s in interrupted]
    sv2.recover_session(sid)
    assert committed <= {s.id for s in repo2.list_segments(sid)}
    assert repo2.get_session(sid)["status"] == "recovered"
    sv2.shutdown()
