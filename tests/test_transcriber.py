from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import numpy as np
import pytest

from lecturemate.asr.asr_worker import StreamingASR, _segment_language
from lecturemate.asr.segment_committer import SegmentCommitter
from lecturemate.asr.transcriber import FasterWhisperTranscriber


def test_auto_retries_failed_cuda_transcription_on_cpu(monkeypatch):
    devices = []

    class FakeModel:
        def __init__(self, _model_size, *, device, **_kwargs):
            self.device = device
            devices.append(device)

        def transcribe(self, *_args, **_kwargs):
            if self.device == "auto":
                def fail_on_iteration():
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                    yield  # pragma: no cover - makes this a generator

                return fail_on_iteration(), None
            return iter([SimpleNamespace(start=0.0, end=0.5, text=" hello", no_speech_prob=0.0,
                                         avg_logprob=-0.1)]), None

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=FakeModel))

    transcriber = FasterWhisperTranscriber(device="auto")
    result = transcriber.transcribe(np.ones(16_000, dtype=np.float32), 1000, final=True)

    assert devices == ["auto", "cpu"]
    assert transcriber.device == "cpu"
    assert [(segment.start_ms, segment.end_ms, segment.text) for segment in result] == [(1000, 1500, "hello")]


def test_auto_language_is_passed_through_from_whisper(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, _audio, **kwargs):
            calls.append(kwargs["language"])
            info = SimpleNamespace(language="fr", language_probability=0.93)
            segment = SimpleNamespace(start=0.1, end=0.8, text=" bonjour", no_speech_prob=0.0, avg_logprob=-0.1)
            return iter([segment]), info

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=FakeModel))
    transcriber = FasterWhisperTranscriber(language="auto")

    result = transcriber.transcribe(np.ones(16_000, dtype=np.float32), 0, final=True)

    assert calls == [None]
    assert result[0].language == "fr"
    assert result[0].language_probability == 0.93


def test_language_detection_and_decoder_share_full_encoder_only_within_one_call(monkeypatch):
    encoded_inputs = []

    class FakeModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, features):
            encoded_inputs.append(features.copy())
            return object()

        def transcribe(self, _audio, **_kwargs):
            features = np.zeros((80, 3000), dtype=np.float32)
            features[:, :100] = 1
            detection_features = features.copy()
            detection_features[:, 100] = 0.5
            detected = self.encode(detection_features)

            def segments():
                assert self.encode(features) is detected
                changed = features.copy()
                changed[0, 0] = 2
                assert self.encode(changed) is not detected
                yield SimpleNamespace(start=0.0, end=1.0, text=" hello", no_speech_prob=0.0,
                                      avg_logprob=-0.1)

            return segments(), SimpleNamespace(language="en", language_probability=0.95)

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    transcriber = FasterWhisperTranscriber(device="cpu")
    for final in (False, False, True, True):
        assert transcriber.transcribe(np.ones(16_000, dtype=np.float32), 0, final=final)[0].text == "hello"

    assert len(encoded_inputs) == 8
    assert all(features.shape == (80, 3000) for features in encoded_inputs)
    assert all(np.all(features[:, 100:] == 0) for features in encoded_inputs)
    assert transcriber._encode_state.value is None


def test_encoder_cache_is_isolated_between_concurrent_jobs(monkeypatch):
    barrier = Barrier(2)
    encoded = []

    class FakeModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, _features):
            value = object()
            encoded.append(value)
            return value

        def transcribe(self, audio, **_kwargs):
            features = np.zeros((80, 3000), dtype=np.float32)
            features[:, :100] = audio[0]
            first = self.encode(features)

            def segments():
                barrier.wait(timeout=5)
                assert self.encode(features.copy()) is first
                yield SimpleNamespace(start=0.0, end=1.0, text=" hello", no_speech_prob=0.0,
                                      avg_logprob=-0.1)

            return segments(), SimpleNamespace(language="en", language_probability=0.95)

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    transcriber = FasterWhisperTranscriber(device="cpu")
    transcriber.load()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(transcriber.transcribe, np.full(16_000, value, dtype=np.float32), 0, final=True)
                for value in (1, 2)]
        assert all(job.result(timeout=10)[0].text == "hello" for job in jobs)
    assert len(encoded) == 2 and encoded[0] is not encoded[1]


def test_encoder_cache_is_released_when_recognition_fails(monkeypatch):
    class FakeModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            raise RuntimeError("recognition failed")

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    transcriber = FasterWhisperTranscriber(device="cpu")
    with pytest.raises(RuntimeError, match="recognition failed"):
        transcriber.transcribe(np.ones(16_000, dtype=np.float32), 0, final=False)
    assert transcriber._encode_state.value is None


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
