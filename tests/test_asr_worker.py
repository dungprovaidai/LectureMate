import queue

import numpy as np

from lecturemate.asr.asr_worker import ASRWorker, StreamingASR
from lecturemate.asr.segment_committer import SegmentCommitter
from lecturemate.asr.transcriber import FakeTranscriber
from lecturemate.audio.audio_worker import END, AudioFrame


def test_backlog_skips_obsolete_previews_without_losing_audio():
    audio = (0.3 * np.sin(2 * np.pi * 200 * np.arange(32_000) / 16_000)).astype(np.float32)
    events = []
    engine = StreamingASR(FakeTranscriber([(100, 500, "hello"), (1200, 1500, "world")]),
                          SegmentCommitter(None, "S"), events.append, partial_interval_s=0.5)
    frames = queue.Queue()
    for start in range(0, len(audio), 1600):
        frames.put(AudioFrame(audio[start:start + 1600], start, 0))
    frames.put(END)

    ASRWorker(frames, engine).run()

    assert [event.type for event in events] == ["final"]
    assert events[0].text_en == "hello world"
    assert engine.stats.audio_ms == 2000
    assert engine.stats.skipped_partials == 20
