import queue
import threading
import wave

import numpy as np

from lecturemate.audio.audio_worker import END, FLUSH, AudioCaptureWorker, AudioFrame, RingBuffer
from lecturemate.audio.resample import Resampler
from lecturemate.audio.sources import FakeAudioSource, FileAudioSource
from lecturemate.audio.wav_writer import WavWriter, read_wav_mono, repair_wav


def _drain(q):
    items = []
    while True:
        it = q.get(timeout=2)
        items.append(it)
        if it is END:
            return items


def test_worker_writes_wav_and_frames_off_ui_thread(tmp_path):
    src = FakeAudioSource([(1.0, 0.3), (0.5, 0.0)])
    q = queue.Queue(maxsize=100)
    wav = WavWriter(tmp_path / "s.wav")
    threads = []
    w = AudioCaptureWorker(src, q, wav, on_level=lambda db: threads.append(threading.current_thread().name))
    w.start()
    items = _drain(q)
    w.join(2)
    frames = [i for i in items if isinstance(i, AudioFrame)]
    assert sum(len(f.samples) for f in frames) == 24000
    assert frames[1].start_sample == 1600
    assert set(threads) == {"AudioCapture"}
    with wave.open(str(tmp_path / "s.wav")) as wf:
        assert wf.getframerate() == 16000 and wf.getnchannels() == 1 and wf.getnframes() == 24000
    assert src.closed


def test_pause_excludes_audio_and_sends_flush(tmp_path):
    src = FakeAudioSource([(3.0, 0.3)], realtime=True)
    q = queue.Queue(maxsize=1000)
    w = AudioCaptureWorker(src, q, WavWriter(tmp_path / "p.wav"))
    w.start()
    import time

    time.sleep(0.5)
    w.pause()
    time.sleep(0.6)
    w.resume()
    items = _drain(q)
    assert FLUSH in items
    kept = sum(len(i.samples) for i in items if isinstance(i, AudioFrame))
    assert kept < 3.0 * 16000 - 0.4 * 16000  # phần tạm dừng không được ghi
    starts = [i.start_sample for i in items if isinstance(i, AudioFrame)]
    assert starts == sorted(starts) and np.all(np.diff(starts) == 1600)  # timeline WAV liên tục


def test_device_loss_reports_error_and_finalizes_wav(tmp_path):
    src = FakeAudioSource([(5.0, 0.3)], fail_after_s=1.0)
    errors = []
    q = queue.Queue(maxsize=100)
    w = AudioCaptureWorker(src, q, WavWriter(tmp_path / "e.wav"), on_error=errors.append)
    w.start()
    _drain(q)
    w.join(2)
    assert errors and "rút" in errors[0]
    x, rate = read_wav_mono(tmp_path / "e.wav")
    assert rate == 16000 and len(x) == 16000


def test_full_queue_drops_oldest_but_wav_complete(tmp_path):
    src = FakeAudioSource([(2.0, 0.3)])
    q = queue.Queue(maxsize=3)
    w = AudioCaptureWorker(src, q, WavWriter(tmp_path / "f.wav"))
    w.start()
    w.join(3)
    assert w.dropped_frames > 0
    x, _ = read_wav_mono(tmp_path / "f.wav")
    assert len(x) == 32000
    assert END in list(q.queue)


def test_open_error(tmp_path):
    src = FileAudioSource(tmp_path / "missing.wav")
    errs = []
    w = AudioCaptureWorker(src, None, None, on_error=errs.append)
    w.start(); w.join(2)
    assert w.open_error and errs


def test_wav_repair_after_crash(tmp_path):
    p = tmp_path / "c.wav"
    w = WavWriter(p, header_every_s=100)  # header gần như không được cập nhật
    w.write(np.zeros(16000, np.float32))
    w._f.flush()  # "crash": không close
    assert repair_wav(p)
    x, _ = read_wav_mono(p)
    assert len(x) == 16000


def test_resampler_48k_to_16k_length_and_tone():
    r = Resampler(48000, 16000)
    t = np.arange(48000) / 48000
    x = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = np.concatenate([r.process(x[i:i + 4800]) for i in range(0, 48000, 4800)])
    assert abs(len(out) - 16000) <= 2
    spec = np.abs(np.fft.rfft(out[1000:15000]))
    peak_hz = np.argmax(spec) * 16000 / len(out[1000:15000])
    assert abs(peak_hz - 440) < 5


def test_ring_buffer():
    rb = RingBuffer(0.001)  # 16 mẫu
    rb.push(np.arange(10, dtype=np.float32))
    rb.push(np.arange(10, 20, dtype=np.float32))
    assert rb.get().tolist() == list(range(4, 20))
