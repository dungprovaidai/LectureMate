from types import SimpleNamespace

from lecturemate.audio.sources import SoundCardSource, list_devices


def test_default_stereo_mix_prefers_physical_microphone(monkeypatch):
    array = SimpleNamespace(id="array-id", name="Microphone Array (Laptop)", isloopback=False)
    stereo_mix = SimpleNamespace(id="mix-id", name="Stereo Mix (Realtek Audio)", isloopback=False)
    speaker = SimpleNamespace(id="speaker-id", name="Speakers", isloopback=True)
    fake_soundcard = SimpleNamespace(
        default_microphone=lambda: stereo_mix,
        default_speaker=lambda: speaker,
        all_microphones=lambda include_loopback=True: [array, stereo_mix, speaker]
        if include_loopback else [array, stereo_mix],
    )
    monkeypatch.setitem(__import__("sys").modules, "soundcard", fake_soundcard)

    source = SoundCardSource("microphone")
    devices = list_devices()

    assert source._pick() is array
    assert next(d for d in devices if d.id == "array-id").is_default
    assert not next(d for d in devices if d.id == "mix-id").is_default
    assert next(d for d in devices if d.id == "mix-id").kind == "microphone"


def test_explicit_microphone_selection_is_preserved(monkeypatch):
    array = SimpleNamespace(id="array-id", name="Microphone Array", isloopback=False)
    mix = SimpleNamespace(id="mix-id", name="Stereo Mix", isloopback=False)
    fake_soundcard = SimpleNamespace(
        get_microphone=lambda device_id: {"array-id": array, "mix-id": mix}[device_id],
        default_microphone=lambda: mix,
        all_microphones=lambda include_loopback=True: [array, mix],
        default_speaker=lambda: None,
    )
    monkeypatch.setitem(__import__("sys").modules, "soundcard", fake_soundcard)

    assert SoundCardSource("microphone", "mix-id")._pick() is mix
