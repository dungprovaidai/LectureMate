import pytest

from lecturemate.domain.models import SessionState as S
from lecturemate.session_controller import InvalidTransition, LectureClock, SessionController


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_happy_path_and_clock(repo):
    ft = FakeTime()
    sc = SessionController(repo, now=ft)
    seen = []
    sc.on_change(lambda a, b: seen.append(b))
    sid = sc.begin("Bài 1", "microphone")
    sc.device_ok()
    ft.t = 10
    sc.pause()
    ft.t = 100  # thời gian tạm dừng không tính
    sc.resume()
    ft.t = 105
    sc.stop()
    sc.complete()
    assert seen == [S.CHECKING_DEVICE, S.RECORDING, S.PAUSED, S.RECORDING, S.STOPPING, S.COMPLETED]
    assert sc.elapsed_ms() == 15_000
    row = repo.get_session(sid)
    assert row["status"] == "completed" and row["duration_ms"] == 15_000 and row["ended_at"]


@pytest.mark.parametrize("bad", ["pause", "stop", "resume", "complete"])
def test_invalid_from_idle(repo, bad):
    sc = SessionController(repo)
    with pytest.raises(InvalidTransition):
        getattr(sc, bad)()


def test_cannot_begin_twice(repo):
    sc = SessionController(repo)
    sc.begin("a", "microphone")
    with pytest.raises(InvalidTransition):
        sc.begin("b", "microphone")


def test_cannot_complete_while_recording(repo):
    sc = SessionController(repo)
    sc.begin("a", "microphone")
    sc.device_ok()
    with pytest.raises(InvalidTransition):
        sc.complete()


def test_interrupted_session_detected_and_recovered(repo):
    sc = SessionController(repo)
    sid = sc.begin("a", "microphone")
    sc.device_ok()
    # "crash": controller mất, chỉ còn DB
    found = SessionController.find_interrupted(repo)
    assert [f["id"] for f in found] == [sid]
    SessionController.recover(repo, sid)
    assert repo.get_session(sid)["status"] == "recovered"
    assert SessionController.find_interrupted(repo) == []


def test_listener_exception_does_not_break(repo):
    sc = SessionController(repo)
    sc.on_change(lambda a, b: 1 / 0)
    sc.begin("a", "microphone")
    assert sc.state == S.CHECKING_DEVICE


def test_clock_basic():
    ft = FakeTime()
    c = LectureClock(ft)
    c.start(); ft.t = 2.5; c.pause(); ft.t = 9
    assert c.elapsed_ms() == 2500
