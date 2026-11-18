"""Hàng đợi dịch có backpressure, retry giới hạn và an toàn khi mất mạng.

- Tối đa ``max_in_flight`` (mặc định 2) yêu cầu đồng thời; khi gặp 429 giảm còn 1 và chờ
  (Retry-After hoặc backoff), sau 5 lần thành công liên tiếp tăng dần trở lại.
- Hàng đợi tồn quá ``max_backlog`` câu → câu cũ nhất được hoãn (vẫn ``pending`` trong DB)
  để phụ đề trực tiếp theo kịp; dịch bù bằng ``retry_pending`` sau buổi hoặc khi có mạng lại.
- Mỗi segment được ``claim`` trong DB trước khi gửi → không bao giờ gửi trùng một câu.
- Mất mạng: không dừng ghi âm/ASR; câu chuyển ``pending`` và tự dịch lại khi có mạng.
"""
from __future__ import annotations

import collections
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ..domain.models import TranslationRequest, TranslationResult, TranslationStatus
from .glossary import Glossary, quality_warnings
from .llm_provider import LLMError, LLMProvider
from .translator import build_messages, clean_output

log = logging.getLogger(__name__)


@dataclass
class Job:
    segment_id: str
    session_id: str
    enqueued_ns: int
    boundary_ns: int = 0
    asr_done_ns: int = 0


def cost_usd(in_tok: int, out_tok: int, price_in: float, price_out: float) -> float:
    return in_tok / 1e6 * price_in + out_tok / 1e6 * price_out


class TranslationQueue:
    def __init__(
        self,
        repo,
        provider_getter: Callable[[], LLMProvider | None],
        glossary_getter: Callable[[str], Glossary] = lambda sid: Glossary(),
        on_result: Callable[[str, TranslationResult], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        max_in_flight: int = 2,
        max_retries: int = 2,
        timeout_s: float = 10.0,
        max_tokens: int = 256,
        context_sentences: int = 2,
        max_backlog: int = 8,
        price_in: float = 0.0,
        price_out: float = 0.0,
        monthly_budget_usd: float = 0.0,
        backoff_base_s: float = 0.5,
        offline_cooldown_s: float = 10.0,
        asr_model: str = "",
    ):
        self.repo = repo
        self.provider_getter = provider_getter
        self.glossary_getter = glossary_getter
        self.on_result = on_result
        self.on_status = on_status
        self.max_in_flight = max(1, max_in_flight)
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.context_sentences = context_sentences
        self.max_backlog = max_backlog
        self.price_in, self.price_out = price_in, price_out
        self.budget = monthly_budget_usd
        self.backoff_base = backoff_base_s
        self.offline_cooldown = offline_cooldown_s
        self.asr_model = asr_model

        self._jobs: collections.deque[Job] = collections.deque()
        self._queued_ids: set[str] = set()
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._limit = self.max_in_flight
        self._active = 0
        self._cooldown_until = 0.0
        self._streak = 0
        self._was_offline = False
        self._threads: list[threading.Thread] = []
        self.status = "idle"
        self.deferred = 0
        self.sent = 0
        self.active_session: str | None = None

    # -------------------------------------------------------------- control
    def start(self) -> None:
        self._stop.clear()
        for i in range(self.max_in_flight):
            t = threading.Thread(target=self._worker, name=f"Translate-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, drain_s: float = 4.0) -> int:
        """Chờ tối đa ``drain_s`` giây cho các câu đang chờ; phần còn lại giữ pending trong DB."""
        deadline = time.monotonic() + drain_s
        with self._cv:
            while (self._jobs or self._active) and time.monotonic() < deadline:
                self._cv.wait(0.1)
            left = len(self._jobs)
            self._jobs.clear()
            self._queued_ids.clear()
            self._stop.set()
            self._cv.notify_all()
        for t in self._threads:
            t.join(timeout=max(0.5, self.timeout_s + 1) if self._active else 0.5)
        self._threads = []
        return left

    def submit(self, segment_id: str, session_id: str, boundary_ns: int = 0, asr_done_ns: int = 0) -> None:
        with self._cv:
            if segment_id in self._queued_ids:
                return
            if len(self._jobs) >= self.max_backlog:
                old = self._jobs.popleft()
                self._queued_ids.discard(old.segment_id)
                self.deferred += 1  # vẫn pending trong DB → dịch bù sau
            self._jobs.append(Job(segment_id, session_id, time.monotonic_ns(), boundary_ns, asr_done_ns))
            self._queued_ids.add(segment_id)
            self._cv.notify()

    def retry_pending(self, session_id: str | None = None, include_skipped: bool = False) -> int:
        """Đưa các câu pending/failed (và skipped nếu muốn) vào hàng đợi, theo thứ tự."""
        rows = self.repo.pending_translations(session_id, include_failed=True, include_skipped=include_skipped)
        n = 0
        with self._cv:
            for r in rows:
                if r.id in self._queued_ids:
                    continue
                self._jobs.append(Job(r.id, r.session_id, time.monotonic_ns()))
                self._queued_ids.add(r.id)
                n += 1
            self._cv.notify_all()
        return n

    def backlog(self) -> int:
        with self._cv:
            return len(self._jobs) + self._active

    def _set_status(self, s: str) -> None:
        if s != self.status:
            self.status = s
            if self.on_status:
                try:
                    self.on_status(s)
                except Exception:
                    log.exception("on_status lỗi")

    # --------------------------------------------------------------- worker
    def _next_job(self) -> Job | None:
        with self._cv:
            while not self._stop.is_set():
                now = time.monotonic()
                if self._jobs and self._active < self._limit and now >= self._cooldown_until:
                    job = self._jobs.popleft()
                    self._queued_ids.discard(job.segment_id)
                    self._active += 1
                    return job
                wait = 0.2 if not self._jobs else max(0.05, min(0.5, self._cooldown_until - now))
                self._cv.wait(wait)
            return None

    def _done(self) -> None:
        with self._cv:
            self._active -= 1
            self._cv.notify_all()

    def _worker(self) -> None:
        while True:
            job = self._next_job()
            if job is None:
                return
            try:
                self._process(job)
            except Exception:
                log.exception("Lỗi không lường trước khi dịch")
                try:
                    self.repo.release_translation(job.segment_id, TranslationStatus.FAILED, "internal")
                except Exception:
                    pass
            finally:
                self._done()

    def _sleep(self, s: float) -> bool:
        """Ngủ có thể bị ngắt bởi stop. Trả False nếu bị dừng."""
        return not self._stop.wait(s)

    def _emit(self, res: TranslationResult) -> None:
        if self.on_result:
            try:
                self.on_result(res.segment_id, res)
            except Exception:
                log.exception("on_result lỗi")

    def _process(self, job: Job) -> None:
        provider = self.provider_getter()
        if provider is None:
            self._set_status("disabled")
            self.repo.release_translation(job.segment_id, TranslationStatus.PENDING, "no_key")
            self._emit(TranslationResult(job.segment_id, None, TranslationStatus.PENDING, error_code="no_key"))
            return
        if self.budget > 0 and self.repo.month_cost_usd() >= self.budget:
            self._set_status("budget")
            self.repo.release_translation(job.segment_id, TranslationStatus.PENDING, "budget")
            self._emit(TranslationResult(job.segment_id, None, TranslationStatus.PENDING, error_code="budget"))
            return
        if not self.repo.claim_translation(job.segment_id):
            return  # đã dịch / đang dịch ở luồng khác
        seg = self.repo.get_segment(job.segment_id)
        if seg is None:
            return
        gl = self.glossary_getter(seg.session_id)
        terms = gl.relevant(seg.en_text)
        ctx = self.repo.preceding_texts(seg.session_id, seg.sequence_number, self.context_sentences)
        req = TranslationRequest(seg.id, seg.session_id, seg.en_text, tuple(ctx),
                                 tuple((t.en, t.vi) for t in terms), int(self.timeout_s * 1000))
        messages = build_messages(req)
        context_chars = sum(len(m["content"]) for m in messages)

        attempt = 0
        while True:
            attempt += 1
            sent_ns = time.monotonic_ns()
            try:
                self.sent += 1
                resp = provider.complete(messages, max_tokens=self.max_tokens, timeout_s=self.timeout_s)
            except LLMError as e:
                self._streak = 0
                if e.code == "rate_limit":
                    with self._cv:
                        self._limit = 1
                        wait = e.retry_after_s or self.backoff_base * (2 ** attempt)
                        self._cooldown_until = max(self._cooldown_until, time.monotonic() + wait)
                    self._set_status("retry")
                elif e.code in ("offline", "timeout"):
                    self._set_status("offline" if e.code == "offline" else "retry")
                elif e.code == "auth":
                    self._set_status("auth_error")
                self.repo.record_request(seg.session_id, seg.id, "translate",
                                         TranslationResult(seg.id, None, TranslationStatus.FAILED,
                                                           elapsed_ms=int((time.monotonic_ns() - sent_ns) / 1e6)), 0.0)
                if not e.retryable or attempt > self.max_retries or self._stop.is_set():
                    if e.code == "offline":
                        self._was_offline = True
                        with self._cv:
                            self._cooldown_until = max(self._cooldown_until, time.monotonic() + self.offline_cooldown)
                    status = TranslationStatus.PENDING if e.code in ("offline", "timeout", "rate_limit", "server") else TranslationStatus.FAILED
                    self.repo.release_translation(seg.id, status, e.code)
                    self._emit(TranslationResult(seg.id, None, status, error_code=e.code, attempts=attempt))
                    return
                backoff = self.backoff_base * (2 ** (attempt - 1)) * (0.5 + random.random())
                if e.code == "rate_limit" and e.retry_after_s:
                    backoff = max(backoff, e.retry_after_s)
                if not self._sleep(backoff):
                    self.repo.release_translation(seg.id, TranslationStatus.PENDING, "stopped")
                    return
                continue
            break

        done_ns = time.monotonic_ns()
        text = clean_output(resp.text)
        if not text:
            self.repo.release_translation(seg.id, TranslationStatus.FAILED, "empty")
            self._emit(TranslationResult(seg.id, None, TranslationStatus.FAILED, error_code="empty"))
            return
        warnings = quality_warnings(seg.en_text, text, gl)
        res = TranslationResult(seg.id, text, TranslationStatus.DONE, model=resp.model,
                                input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
                                elapsed_ms=resp.total_ms, first_token_ms=resp.ttft_ms, attempts=attempt,
                                extra={"warnings": warnings, "usage_estimated": resp.usage_estimated})
        self.repo.save_translation(res)
        if warnings:
            self.repo.set_flag(seg.id, True)
        self.repo.record_request(seg.session_id, seg.id, "translate", res,
                                 cost_usd(resp.input_tokens, resp.output_tokens, self.price_in, self.price_out))
        lat = {"api_ms": int((done_ns - sent_ns) / 1e6), "queue_ms": int((sent_ns - job.enqueued_ns) / 1e6),
               "asr_model": self.asr_model, "context_chars": context_chars}
        if job.boundary_ns and job.asr_done_ns:
            lat["asr_ms"] = int((job.asr_done_ns - job.boundary_ns) / 1e6)
            lat["total_ms"] = int((done_ns - job.boundary_ns) / 1e6)
        self.repo.record_latency(seg.id, seg.session_id, **lat)
        res.extra["latency"] = lat
        self._streak += 1
        with self._cv:
            if self._streak >= 5 and self._limit < self.max_in_flight:
                self._limit += 1
                self._streak = 0
        self._set_status("connected")
        self._emit(res)
        if self._was_offline:
            self._was_offline = False
            if self.active_session:
                self.retry_pending(self.active_session)
