import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LECTUREMATE_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture()
def repo(tmp_path):
    from lecturemate.persistence.repository import SessionRepository

    audio = tmp_path / "audio"
    audio.mkdir()
    r = SessionRepository(tmp_path / "t.db", audio_root=audio)
    yield r
    r.close()


def make_final(session_id, seq, text, start=None, end=None):
    from lecturemate.domain.models import TranscriptEvent

    start = seq * 1000 if start is None else start
    end = start + 900 if end is None else end
    return TranscriptEvent("final", session_id, f"{session_id}-{seq}", start, end, text,
                           sequence_number=seq)
