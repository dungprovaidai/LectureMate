import sqlite3

import pytest

from conftest import make_final
from lecturemate.domain.models import SessionState, TranscriptEvent, TranslationResult, TranslationStatus
from lecturemate.persistence.db import connect


def test_migrations_idempotent(tmp_path):
    c1 = connect(tmp_path / "a.db")
    v1 = [r[0] for r in c1.execute("SELECT version FROM schema_version")]
    c1.close()
    c2 = connect(tmp_path / "a.db")
    v2 = [r[0] for r in c2.execute("SELECT version FROM schema_version")]
    assert v1 == v2 == [1, 2]
    assert c2.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c2.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_commit_final_once(repo):
    sid = repo.create_session("t", "microphone")
    ev = make_final(sid, 0, "Hello world")
    assert repo.commit_final(ev) is True
    assert repo.commit_final(ev) is False  # cùng id
    assert len(repo.list_segments(sid)) == 1


def test_unique_session_sequence(repo):
    sid = repo.create_session("t", "microphone")
    repo.commit_final(make_final(sid, 0, "a"))
    dup = TranscriptEvent("final", sid, "other-id", 0, 10, "b", sequence_number=0)
    assert repo.commit_final(dup) is False
    assert [s.en_text for s in repo.list_segments(sid)] == ["a"]


def test_partial_cannot_be_committed(repo):
    sid = repo.create_session("t", "microphone")
    with pytest.raises(ValueError):
        repo.commit_final(TranscriptEvent("partial", sid, "x", 0, 1, "hi"))


def test_delete_session_isolated(repo, tmp_path):
    a = repo.create_session("A", "microphone")
    b = repo.create_session("B", "microphone")
    fa = repo.audio_root / "a.wav"
    fb = repo.audio_root / "b.wav"
    fa.write_bytes(b"x")
    fb.write_bytes(b"y")
    repo.set_audio_path(a, str(fa))
    repo.set_audio_path(b, str(fb))
    for i in range(3):
        repo.commit_final(make_final(a, i, f"a{i}"))
        repo.commit_final(make_final(b, i, f"b{i}"))
    ok, _ = repo.delete_session(a)
    assert ok
    assert not fa.exists() and fb.exists()
    assert repo.get_session(a) is None
    assert len(repo.list_segments(b)) == 3
    n = repo.connection.execute("SELECT count(*) FROM translations").fetchone()[0]
    assert n == 3  # cascade chỉ xóa của A


def test_delete_refuses_path_outside_audio_root(repo, tmp_path):
    outside = tmp_path / "important.txt"
    outside.write_text("keep")
    sid = repo.create_session("A", "microphone")
    repo.set_audio_path(sid, str(repo.audio_root / ".." / "important.txt"))
    ok, _ = repo.delete_session(sid)
    assert ok
    assert outside.exists()


def test_claim_translation_prevents_double_send(repo):
    sid = repo.create_session("t", "microphone")
    ev = make_final(sid, 0, "x")
    repo.commit_final(ev)
    assert repo.claim_translation(ev.segment_id)
    assert not repo.claim_translation(ev.segment_id)
    repo.save_translation(TranslationResult(ev.segment_id, "xin chào", TranslationStatus.DONE, model="m"))
    assert not repo.claim_translation(ev.segment_id)
    assert repo.get_segment(ev.segment_id).vi_text == "xin chào"


def test_unfinished_detection(repo):
    sid = repo.create_session("t", "microphone")
    repo.update_status(sid, SessionState.RECORDING)
    assert [s["id"] for s in repo.find_unfinished()] == [sid]
    repo.update_status(sid, SessionState.COMPLETED)
    assert repo.find_unfinished() == []


def test_vietnamese_unicode_roundtrip(repo):
    sid = repo.create_session("Kinh tế vĩ mô — buổi 1", "loopback")
    ev = make_final(sid, 0, "Inflation")
    repo.commit_final(ev)
    repo.save_translation(TranslationResult(ev.segment_id, "Lạm phát tăng ở mức 3,5%", TranslationStatus.DONE))
    assert repo.get_session(sid)["title"] == "Kinh tế vĩ mô — buổi 1"
    assert repo.get_segment(ev.segment_id).vi_text == "Lạm phát tăng ở mức 3,5%"


def test_committed_survives_unclean_close(tmp_path):
    """Mô phỏng crash: không close() connection, mở kết nối mới vẫn thấy dữ liệu."""
    from lecturemate.persistence.repository import SessionRepository

    r1 = SessionRepository(tmp_path / "c.db")
    sid = r1.create_session("t", "microphone")
    r1.commit_final(make_final(sid, 0, "persisted"))
    # không gọi r1.close()
    raw = sqlite3.connect(tmp_path / "c.db")
    assert raw.execute("SELECT en_text FROM segments").fetchone()[0] == "persisted"
