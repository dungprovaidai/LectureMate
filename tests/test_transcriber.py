from types import SimpleNamespace

import numpy as np

from lecturemate.asr.asr_worker import StreamingASR, _segment_language
from lecturemate.asr.segment_committer import SegmentCommitter
from lecturemate.asr.transcriber import FasterWhisperTranscriber


def test_auto_retries_failed_cuda_transcription_on_cpu(monkeypatch):
    devices = []

    class FakeModel:
        def __init__(self, device):
            self.device = device

        def transcribe(self, *_args, **_kwargs):
            if self.device == "auto":
                def fail_on_iteration():
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                    yield  # pragma: no cover - makes this a generator

                return fail_on_iteration(), None
            return iter([SimpleNamespace(start=0.0, end=0.5, text=" hello", no_speech_prob=0.0,
                                         avg_logprob=-0.1)]), None

    def create_model(_model_size, *, device, **_kwargs):
        devices.append(device)
        return FakeModel(device)

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=create_model))

    transcriber = FasterWhisperTranscriber(device="auto")
    result = transcriber.transcribe(np.ones(16_000, dtype=np.float32), 1000, final=True)

    assert devices == ["auto", "cpu"]
    assert transcriber.device == "cpu"
    assert [(segment.start_ms, segment.end_ms, segment.text) for segment in result] == [(1000, 1500, "hello")]


def test_auto_language_is_passed_through_from_whisper(monkeypatch):
    calls = []

    class FakeModel:
        def transcribe(self, _audio, **kwargs):
            calls.append(kwargs["language"])
            info = SimpleNamespace(language="fr", language_probability=0.93)
            segment = SimpleNamespace(start=0.1, end=0.8, text=" bonjour", no_speech_prob=0.0, avg_logprob=-0.1)
            return iter([segment]), info

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: FakeModel()))
    transcriber = FasterWhisperTranscriber(language="auto")

    result = transcriber.transcribe(np.ones(16_000, dtype=np.float32), 0, final=True)

    assert calls == [None]
    assert result[0].language == "fr"
    assert result[0].language_probability == 0.93


def test_language_confidence_is_aggregated_by_transcript_length():
    segments = [
        SimpleNamespace(language="fr", language_probability=0.9, text="long French segment"),
        SimpleNamespace(language="en", language_probability=0.99, text="ok"),
    ]

    assert _segment_language(segments) == ("fr", 0.9)


def test_streaming_asr_reports_final_error_once():
    class BrokenTranscriber:
        def transcribe(self, *_args, **_kwargs):
            raise RuntimeError("model unavailable")

    errors = []
    engine = StreamingASR(BrokenTranscriber(), SegmentCommitter(None, "session"), lambda _event: None,
                          on_error=errors.append)
    audio = np.ones(16_000, dtype=np.float32)

    engine._transcribe_final(audio, 0, 1.0)
    engine._transcribe_final(audio, 16_000, 1.0)

    assert errors == ["Lỗi ASR — nhận dạng thất bại: model unavailable"]
