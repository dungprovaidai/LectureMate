"""Giao diện LLMProvider dùng chung cho dịch, ghi chú, hỏi đáp + MockProvider."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    usage_estimated: bool = False  # True khi provider không trả usage
    ttft_ms: int | None = None
    total_ms: int = 0
    raw: dict = field(default_factory=dict)


class LLMError(Exception):
    """code: auth | rate_limit | server | timeout | offline | bad_request | bad_response | budget"""

    def __init__(self, code: str, message: str = "", retryable: bool = False,
                 retry_after_s: float | None = None, status: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.status = status


class LLMProvider(Protocol):
    model: str

    def complete(self, messages: list[dict], *, max_tokens: int = 256, timeout_s: float = 10.0,
                 temperature: float = 0.2) -> LLMResponse: ...


def estimate_tokens(text: str) -> int:
    """Ước lượng thô ~4 ký tự/token (tiếng Việt có dấu thường tốn hơn). Chỉ dùng khi thiếu usage."""
    return max(1, len(text) // 4)


class MockProvider:
    """Provider giả: hàm ``responder(messages) -> str`` hoặc chuỗi lỗi kịch bản.

    ``script`` là danh sách các phần tử: str (trả lời) hoặc LLMError (ném ra), dùng lần lượt;
    hết script thì dùng ``responder``.
    """

    def __init__(self, responder: Callable[[list[dict]], str] | None = None, latency_s: float = 0.0,
                 script: list | None = None, model: str = "mock-model"):
        self.responder = responder or (lambda m: "Bản dịch giả: " + m[-1]["content"][-60:])
        self.latency = latency_s
        self.script = list(script or [])
        self.model = model
        self.calls: list[list[dict]] = []
        self._lock = threading.Lock()
        self.max_concurrent = 0
        self._active = 0

    def complete(self, messages, *, max_tokens=256, timeout_s=10.0, temperature=0.2) -> LLMResponse:
        with self._lock:
            self.calls.append(messages)
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
            item = self.script.pop(0) if self.script else None
        try:
            t0 = time.perf_counter()
            if self.latency:
                time.sleep(self.latency)
            if isinstance(item, Exception):
                raise item
            text = item if isinstance(item, str) else self.responder(messages)
            prompt = "".join(m["content"] for m in messages)
            ms = int((time.perf_counter() - t0) * 1000)
            return LLMResponse(text, self.model, estimate_tokens(prompt), estimate_tokens(text),
                               ttft_ms=ms, total_ms=ms)
        finally:
            with self._lock:
                self._active -= 1
