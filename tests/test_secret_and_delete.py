"""P5.1.A.1 — Không lộ token trong log; người dùng xóa được phiên (kể cả file)."""
import logging

import httpx

from conftest import make_final
from lecturemate.ai import secrets
from lecturemate.ai.agnes_adapter import AgnesAdapter

KEY = "agn-SECRET-abcdef123456"


def test_setup_logging_redacts(home, monkeypatch, tmp_path):
    monkeypatch.setenv("LECTUREMATE_API_KEY", KEY)
    from lecturemate.__main__ import setup_logging
    from lecturemate.config import logs_dir

    root = logging.getLogger()
    before = list(root.handlers)
    setup_logging()
    try:
        log = logging.getLogger("lecturemate.x")
        log.error("request failed key=%s", KEY)
        log.error("Authorization: Bearer %s", KEY)
        # adapter lỗi HTTP cũng không đưa key vào thông điệp
        a = AgnesAdapter("https://x.test/v1", KEY, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        try:
            a.complete([{"role": "user", "content": "hi"}])
        except Exception as e:
            log.error("api error: %s / %r", e, a)
        for h in root.handlers:
            h.flush()
        text = (logs_dir() / "lecturemate.log").read_text(encoding="utf-8")
        assert KEY not in text and "REDACTED" in text and "api error" in text
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_config_never_contains_key(home, monkeypatch):
    from lecturemate.config import AppConfig, config_path

    monkeypatch.setenv("LECTUREMATE_API_KEY", KEY)
    cfg = AppConfig()
    cfg.save()
    assert KEY not in config_path().read_text(encoding="utf-8")
    assert secrets.get_api_key() == KEY


def test_user_can_delete_session_with_audio(repo):
    sid = repo.create_session("Riêng tư", "microphone")
    wav = repo.audio_root / "x.wav"
    wav.write_bytes(b"RIFF....")
    repo.set_audio_path(sid, str(wav))
    repo.commit_final(make_final(sid, 0, "secret lecture content"))
    repo.save_notes(sid, "notes", "m", [])
    ok, msg = repo.delete_session(sid)
    assert ok and not wav.exists()
    c = repo.connection
    for table in ("segments", "translations", "notes", "latency", "request_metrics"):
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM segment_fts WHERE en_text LIKE '%secret%'").fetchone()[0] == 0


def test_locked_file_marks_pending_cleanup(repo, monkeypatch):
    from pathlib import Path

    sid = repo.create_session("Khóa file", "microphone")
    wav = repo.audio_root / "locked.wav"
    wav.write_bytes(b"x")
    repo.set_audio_path(sid, str(wav))
    orig = Path.unlink

    def boom(self, *a, **k):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "unlink", boom)
    ok, msg = repo.delete_session(sid)
    assert not ok and "thử lại" in msg
    assert repo.get_session(sid)["status"] == "pending_delete"
    assert all(s["id"] != sid for s in repo.list_sessions())  # ẩn khỏi thư viện
    monkeypatch.setattr(Path, "unlink", orig)
    assert repo.retry_pending_cleanup() == 1 and repo.get_session(sid) is None
