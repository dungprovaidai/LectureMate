import numpy as np

from lecturemate.asr.asr_worker import StreamingASR
from lecturemate.asr.segment_committer import SegmentCommitter
from lecturemate.audio import SAMPLE_RATE
from lecturemate.audio.audio_worker import AudioFrame, rms_dbfs
from lecturemate.audio.mic_input import MicFilter, boost_quiet_speech


def tone(hz, amplitude, seconds=1):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_mic_filter_attenuates_rumble_and_preserves_stream_length():
    bass = tone(50, 0.1) + 0.02  # tiếng ù + DC offset
    voice = tone(300, 0.1)
    filtered_bass = MicFilter().process(bass)
    filtered_voice = MicFilter().process(voice)
    assert rms_dbfs(filtered_bass[320:]) < rms_dbfs(bass[320:]) - 12
    assert abs(rms_dbfs(filtered_voice[320:]) - rms_dbfs(voice[320:])) < 1

    audio = bass + voice
    mic = MicFilter()
    chunks = np.concatenate([mic.process(x) for x in np.array_split(audio, 27)])
    assert len(chunks) == len(audio) and chunks.dtype == audio.dtype
    np.testing.assert_allclose(chunks, MicFilter().process(audio), atol=1e-7)


def test_gain_is_bounded_and_does_not_clip_or_change_original():
    silence = np.zeros(1600, dtype=np.float32)
    weak = tone(300, 0.001)
    normal = tone(300, 0.25)
    np.testing.assert_array_equal(boost_quiet_speech(silence), silence)
    np.testing.assert_array_equal(boost_quiet_speech(normal), normal)
    boost = rms_dbfs(boost_quiet_speech(weak)) - rms_dbfs(weak)
    assert 10 < boost <= 24.001
    np.testing.assert_array_equal(weak, tone(300, 0.001))

    quiet_with_spike = weak.copy()
    quiet_with_spike[100] = 0.4
    assert 0.4 < np.max(np.abs(boost_quiet_speech(quiet_with_spike))) <= 0.950001


def test_mic_assist_stays_quiet_with_steady_background():
    class NoSpeech:
        def transcribe(self, *_args, **_kwargs):
            raise AssertionError("Không được gửi tiếng nền đều tới ASR")

    events = []
    engine = StreamingASR(NoSpeech(), SegmentCommitter(None, "S"), events.append,
                          partial_interval_s=0.5, mic_assist=True)
    noise = np.random.default_rng(42).normal(0, 0.0019, 3 * SAMPLE_RATE).astype(np.float32)
    audio = noise + tone(50, 0.004, 3)
    for i in range(0, len(audio), 1600):
        engine.feed(AudioFrame(audio[i:i + 1600], i, 0))
    engine.flush()

    assert not events and engine.stats.errors == 0
