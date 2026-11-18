import numpy as np

from conftest import make_final
from lecturemate.asr.asr_worker import StreamingASR
from lecturemate.asr.segment_committer import SegmentCommitter, strip_overlap
from lecturemate.asr.transcriber import FakeTranscriber
from lecturemate.audio.audio_worker import AudioFrame
from lecturemate.domain.models import TranscriptEvent

SR = 16000


def build_lecture(n_sentences=100, speak_s=1.5, gap_s=0.8):
    """Âm thanh: n câu (tone) cách nhau bởi khoảng lặng; script từ tương ứng cho FakeTranscriber."""
    rng = np.random.default_rng(1)
    parts, words, t = [], [], 0.0
    for i in range(n_sentences):
        n = int(speak_s * SR)
        parts.append((0.3 * np.sin(2 * np.pi * 200 * np.arange(n) / SR)).astype(np.float32))
        for k, w in enumerate([f"s{i}", "word", f"w{i}"]):
            ws = int((t + k * 0.4) * 1000)
            words.append((ws, ws + 300, w))
        t += speak_s
        g = int(gap_s * SR)
        parts.append(rng.normal(0, 0.0005, g).astype(np.float32))
        t += gap_s
    return np.concatenate(parts), words


class AudioClock:
    def __init__(self):
        self.ns = 0

    def __call__(self):
        return self.ns


def run_engine(audio, words, repo=None, session_id="S", **kw):
    events = []
    clock = AudioClock()
    committer = SegmentCommitter(repo, session_id, start_sequence=0 if repo is None else None)
    eng = StreamingASR(FakeTranscriber(words), committer, events.append, clock_ns=clock, **kw)
    for i in range(0, len(audio), 1600):
        clock.ns = int((i + 1600) / SR * 1e9)
        eng.feed(AudioFrame(audio[i:i + 1600], i, clock.ns))
    eng.flush()
    return events, committer, eng


def test_100_segments_no_duplicate_sequence(repo):
    sid = repo.create_session("t", "microphone")
    audio, words = build_lecture(100)
    events, committer, eng = run_engine(audio, words, repo=repo, session_id=sid)
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 100
    seqs = [f.sequence_number for f in finals]
    assert seqs == list(range(100))
    rows = repo.list_segments(sid)
    assert len(rows) == 100 and len({r.sequence_number for r in rows}) == 100
    assert rows[5].en_text == "s5 word w5"
    assert eng.stats.realtime_factor < 1


def test_partials_before_final_same_id_but_only_final_saved(repo):
    sid = repo.create_session("t", "microphone")
    audio, words = build_lecture(3, speak_s=3.0)
    events, _, _ = run_engine(audio, words, repo=repo, session_id=sid, partial_interval_s=0.5)
    for f in [e for e in events if e.type == "final"]:
        same = [e for e in events if e.segment_id == f.segment_id]
        assert same[0].type == "partial", "phải có partial trước final"
        assert same[-1].type == "final"
        versions = [e.version for e in same]
        assert versions == sorted(versions)
    db_ids = {r.id for r in repo.list_segments(sid)}
    assert db_ids == {e.segment_id for e in events if e.type == "final"}


def test_timestamps_follow_audio_timeline():
    audio, words = build_lecture(5)
    events, _, _ = run_engine(audio, words)
    finals = [e for e in events if e.type == "final"]
    for i, f in enumerate(finals):
        expected = int(i * 2.3 * 1000)
        assert abs(f.start_ms - expected) <= 1000, (f.start_ms, expected)
        assert f.end_ms > f.start_ms


def test_long_speech_is_cut_without_overlap():
    n = int(30 * SR)
    audio = (0.3 * np.sin(2 * np.pi * 200 * np.arange(n) / SR)).astype(np.float32)
    words = [(i * 500, i * 500 + 400, f"w{i}") for i in range(60)]
    events, committer, _ = run_engine(audio, words, max_segment_s=8.0)
    finals = [e for e in events if e.type == "final"]
    assert len(finals) >= 3
    all_words = " ".join(f.text_en for f in finals).split()
    assert len(all_words) == len(set(all_words)), "không được nhân đôi từ ở biên"
    for a, b in zip(finals, finals[1:]):
        assert b.start_ms >= a.end_ms - 1


def test_silence_only_produces_nothing():
    audio = np.random.default_rng(0).normal(0, 0.0005, 10 * SR).astype(np.float32)
    events, _, _ = run_engine(audio, [])
    assert not [e for e in events if e.type == "final"]


def test_committer_drops_repeat_and_boundary_words():
    c = SegmentCommitter(None, "S", start_sequence=0)
    a = c.commit(TranscriptEvent("final", "S", "1", 0, 1000, "the market is efficient"))
    b = c.commit(TranscriptEvent("final", "S", "2", 1000, 2000, "the market is efficient"))
    d = c.commit(TranscriptEvent("final", "S", "3", 1000, 3000, "is efficient when prices adjust"))
    assert a and b is None
    assert d.text_en == "when prices adjust" and d.sequence_number == 1


def test_repeated_phrase_after_pause_is_kept():
    """Lỗi thật phát hiện khi chạy demo: 'opportunity cost.' rồi 'Opportunity cost is…' bị cắt mất."""
    c = SegmentCommitter(None, "S", start_sequence=0)
    c.commit(TranscriptEvent("final", "S", "1", 0, 2000, "Today we talk about opportunity cost."))
    b = c.commit(TranscriptEvent("final", "S", "2", 2800, 5000, "Opportunity cost is the next best alternative."))
    assert b.text_en.startswith("Opportunity cost is")
    # cắt cứng liền nhau, câu trước chưa hết → vẫn bỏ từ nhân đôi
    c2 = SegmentCommitter(None, "S", start_sequence=0)
    c2.commit(TranscriptEvent("final", "S", "1", 0, 8000, "and the demand curve shifts to"))
    d = c2.commit(TranscriptEvent("final", "S", "2", 8000, 9000, "shifts to the right"))
    assert d.text_en == "the right"


def test_hallucination_filter_only_on_low_speech():
    c = SegmentCommitter(None, "S", start_sequence=0)
    assert c.commit(TranscriptEvent("final", "S", "1", 0, 1000, "Thank you."), speech_ratio=0.1) is None
    assert c.commit(TranscriptEvent("final", "S", "2", 5000, 6000, "Thank you."), speech_ratio=0.9)


def test_strip_overlap():
    assert strip_overlap("we talk about supply and demand", "supply and demand curves") == "curves"
    assert strip_overlap("hello", "world") == "world"


def test_committer_continues_sequence_after_restart(repo):
    sid = repo.create_session("t", "microphone")
    for i in range(3):
        repo.commit_final(make_final(sid, i, f"x{i}"))
    c = SegmentCommitter(repo, sid)
    assert c.next_seq == 3
