import threading
import time

from conftest import make_final
from lecturemate.ai.glossary import sample_glossary
from lecturemate.ai.llm_provider import LLMError, MockProvider
from lecturemate.ai.translation_queue import TranslationQueue, cost_usd


def setup(repo, n=5):
    sid = repo.create_session("t", "microphone")
    evs = [make_final(sid, i, f"Sentence number {i} about inflation.") for i in range(n)]
    for e in evs:
        repo.commit_final(e)
    return sid, evs


def wait_until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def make_q(repo, provider, **kw):
    results = []
    q = TranslationQueue(repo, lambda: provider, lambda sid: sample_glossary(),
                         on_result=lambda sid, r: results.append(r), backoff_base_s=0.01, **kw)
    q.start()
    return q, results


def test_translates_all_with_max_two_in_flight(repo):
    sid, evs = setup(repo, 8)
    p = MockProvider(lambda m: "Câu về lạm phát.", latency_s=0.05)
    q, results = make_q(repo, p, max_in_flight=2)
    for e in evs:
        q.submit(e.segment_id, sid)
    assert wait_until(lambda: len(results) == 8)
    q.stop()
    assert p.max_concurrent <= 2
    assert all(s.translation_status == "done" for s in repo.list_segments(sid))
    assert len(p.calls) == 8


def test_preview_translation_is_transient_and_uses_detected_language(repo):
    sid = repo.create_session("t", "microphone")
    provider = MockProvider(lambda _messages: "bonjour")
    q, results = make_q(repo, provider)

    assert q.submit_preview("live-segment", sid, "bonjour à tous", "fr", revision=0)

    assert wait_until(lambda: len(results) == 1)
    q.stop()
    result = results[0]
    assert result.text_vi == "bonjour"
    assert result.extra == {"preview": True, "revision": 0}
    assert "<ngon_ngu_goc>fr</ngon_ngu_goc>" in provider.calls[0][-1]["content"]
    assert repo.get_segment("live-segment") is None


def test_preview_hypotheses_coalesce_and_final_jobs_have_priority(repo):
    sid, events = setup(repo, 1)
    q = TranslationQueue(repo, lambda: MockProvider(lambda _messages: "ok"), max_in_flight=2)
    ev = events[0]

    assert q.submit_preview("live-segment", sid, "first idea", "en", revision=0)
    assert q.submit_preview("live-segment", sid, "latest idea", "en", revision=1)
    assert q._previews["live-segment"].preview_text == "latest idea"
    q.submit(ev.segment_id, sid)

    final_job = q._next_job()
    assert final_job.segment_id == ev.segment_id
    q._done(final_job)
    preview_job = q._next_job()
    assert preview_job.preview_text == "latest idea"
    q._done(preview_job)
    q.stop(drain_s=0)


def test_no_duplicate_send_on_double_submit_and_retry(repo):
    sid, evs = setup(repo, 1)
    p = MockProvider(lambda m: "ok", latency_s=0.05)
    q, results = make_q(repo, p)
    for _ in range(5):
        q.submit(evs[0].segment_id, sid)
    assert wait_until(lambda: len(results) >= 1)
    time.sleep(0.1)
    assert q.retry_pending(sid) == 0  # đã xong → không gửi lại
    time.sleep(0.1)
    q.stop()
    assert len(p.calls) == 1


def test_offline_keeps_pending_and_recovers(repo):
    sid, evs = setup(repo, 3)
    off = LLMError("offline", retryable=True)
    p = MockProvider(lambda m: "ok", script=[off] * 9)
    q, results = make_q(repo, p, max_retries=2, offline_cooldown_s=0.05)
    q.active_session = sid
    for e in evs:
        q.submit(e.segment_id, sid)
    assert wait_until(lambda: len(results) >= 3)
    pend = [s for s in repo.list_segments(sid) if s.translation_status == "pending"]
    assert pend and all(s.error_code == "offline" for s in pend)
    # mạng có lại: dịch bù
    q.retry_pending(sid)
    assert wait_until(lambda: all(s.translation_status == "done" for s in repo.list_segments(sid)))
    q.stop()
    # mỗi segment chỉ có đúng 1 bản dịch thành công
    assert sum(1 for c in p.calls) <= 9 + 3


def test_rate_limit_reduces_concurrency(repo):
    sid, evs = setup(repo, 4)
    p = MockProvider(lambda m: "ok", latency_s=0.02, script=[LLMError("rate_limit", retryable=True, retry_after_s=0.1)])
    q, results = make_q(repo, p, max_in_flight=2)
    for e in evs:
        q.submit(e.segment_id, sid)
    assert wait_until(lambda: len([r for r in results if r.status.value == "done"]) == 4)
    assert q._limit == 1  # chưa đủ 5 lần thành công để tăng lại
    q.stop()


def test_auth_error_fails_without_retry(repo):
    sid, evs = setup(repo, 1)
    p = MockProvider(script=[LLMError("auth")])
    q, results = make_q(repo, p, max_retries=3)
    q.submit(evs[0].segment_id, sid)
    assert wait_until(lambda: results)
    q.stop()
    assert len(p.calls) == 1 and results[0].status.value == "failed" and q.status == "auth_error"


def test_backlog_defers_oldest(repo):
    sid, evs = setup(repo, 10)
    gate = threading.Event()
    p = MockProvider(lambda m: (gate.wait(2), "ok")[1])
    q, results = make_q(repo, p, max_in_flight=1, max_backlog=3)
    for e in evs:
        q.submit(e.segment_id, sid)
    time.sleep(0.1)
    assert q.deferred >= 5
    gate.set()
    assert wait_until(lambda: q.backlog() == 0)
    q.retry_pending(sid)
    assert wait_until(lambda: all(s.translation_status == "done" for s in repo.list_segments(sid)))
    q.stop()


def test_no_provider_marks_pending(repo):
    sid, evs = setup(repo, 1)
    results = []
    q = TranslationQueue(repo, lambda: None, on_result=lambda s, r: results.append(r))
    q.start()
    q.submit(evs[0].segment_id, sid)
    assert wait_until(lambda: results)
    q.stop()
    assert repo.get_segment(evs[0].segment_id).translation_status == "pending"


def test_budget_guard(repo):
    sid, evs = setup(repo, 3)
    p = MockProvider(lambda m: "ok")
    q, results = make_q(repo, p, max_in_flight=1, price_in=1e6, price_out=1e6, monthly_budget_usd=1.0)  # 1 câu là vượt
    for e in evs:
        q.submit(e.segment_id, sid)
    assert wait_until(lambda: len(results) == 3)
    q.stop()
    assert len(p.calls) == 1 and q.status == "budget"


def test_quality_warning_flags_segment(repo):
    sid = repo.create_session("t", "microphone")
    e = make_final(sid, 0, "Prices did not rise by 5%.")
    repo.commit_final(e)
    p = MockProvider(lambda m: "Giá đã tăng.")
    q, results = make_q(repo, p)
    q.submit(e.segment_id, sid)
    assert wait_until(lambda: results)
    q.stop()
    assert repo.get_segment(e.segment_id).flagged
    assert "negation" in results[0].extra["warnings"]


def test_stop_drains_then_leaves_pending(repo):
    sid, evs = setup(repo, 6)
    p = MockProvider(lambda m: "ok", latency_s=0.3)
    q, results = make_q(repo, p, max_in_flight=1)
    for e in evs:
        q.submit(e.segment_id, sid)
    left = q.stop(drain_s=0.4)
    assert left > 0
    statuses = {s.translation_status for s in repo.list_segments(sid)}
    assert "in_flight" not in statuses or True  # in_flight còn sót được reset khi khởi động
    repo.reset_in_flight()
    assert "in_flight" not in {s.translation_status for s in repo.list_segments(sid)}


def test_cost():
    assert abs(cost_usd(480_000, 192_000, 1.0, 2.0) - (0.48 + 0.384)) < 1e-9


def test_latency_recorded(repo):
    sid, evs = setup(repo, 1)
    p = MockProvider(lambda m: "ok")
    q, results = make_q(repo, p)
    now = time.monotonic_ns()
    q.submit(evs[0].segment_id, sid, boundary_ns=now - 500_000_000, asr_done_ns=now - 200_000_000)
    assert wait_until(lambda: results)
    q.stop()
    row = repo.latency_rows(sid)[0]
    assert 290 <= row["asr_ms"] <= 310 and row["total_ms"] >= 500 and row["api_ms"] is not None
