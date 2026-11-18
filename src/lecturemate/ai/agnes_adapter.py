"""AgnesAdapter — client cho API dạng OpenAI-compatible ``POST {base_url}/chat/completions``.

Nguồn (tra cứu 18/11/2026, CẦN xác minh lại bằng tài khoản của bạn):
catalog AgnesAI-Labs ghi base URL ``https://apihub.agnes-ai.com/v1``, header
``Authorization: Bearer <key>``, endpoint ``/v1/chat/completions``. Catalog KHÔNG liệt kê
riêng ``agnes-3.0-flash``, tham số tắt suy luận hay giá token → để trống/cấu hình được.

- base_url, model, extra_body (tham số riêng provider) đều đến từ cấu hình.
- Không log header/khóa. Lỗi được phân loại để TranslationQueue quyết định retry.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from .llm_provider import LLMError, LLMResponse, estimate_tokens

log = logging.getLogger(__name__)


class AgnesAdapter:
    def __init__(self, base_url: str, api_key: str | None, model: str = "agnes-3.0-flash",
                 stream: bool = True, extra_body: dict | None = None,
                 transport: httpx.BaseTransport | None = None, path: str = "/chat/completions"):
        if not base_url:
            raise ValueError("Chưa cấu hình base URL")
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.model = model
        self.stream = stream
        self.extra_body = dict(extra_body or {})
        self._key = api_key
        self._client = httpx.Client(transport=transport, http2=False, follow_redirects=False)

    def __repr__(self) -> str:  # tránh lộ key khi debug in object
        return f"AgnesAdapter(base_url={self.base_url!r}, model={self.model!r})"

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------ request
    def complete(self, messages: list[dict], *, max_tokens: int = 256, timeout_s: float = 10.0,
                 temperature: float = 0.2) -> LLMResponse:
        if not self._key:
            raise LLMError("auth", "Chưa có API key", retryable=False)
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "stream": self.stream}
        if self.stream:
            body["stream_options"] = {"include_usage": True}
        body.update(self.extra_body)
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   "Accept": "text/event-stream" if self.stream else "application/json"}
        url = self.base_url + self.path
        timeout = httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))
        t0 = time.perf_counter()
        try:
            if self.stream:
                with self._client.stream("POST", url, json=body, headers=headers, timeout=timeout) as r:
                    if r.status_code >= 400:
                        r.read()
                        raise self._http_error(r)
                    return self._read_stream(r, t0, messages)
            r = self._client.post(url, json=body, headers=headers, timeout=timeout)
            if r.status_code >= 400:
                raise self._http_error(r)
            return self._read_json(r.json(), t0, messages)
        except LLMError:
            raise
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", "Hết thời gian chờ API", retryable=True) from exc
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            raise LLMError("offline", "Không kết nối được API", retryable=True) from exc
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("bad_response", f"Phản hồi không đúng định dạng: {exc.__class__.__name__}") from exc

    @staticmethod
    def _http_error(r: httpx.Response) -> LLMError:
        s = r.status_code
        detail = ""
        try:
            j = r.json()
            detail = str(j.get("error", {}).get("message", ""))[:200] if isinstance(j, dict) else ""
        except Exception:
            pass
        if s in (401, 403):
            return LLMError("auth", f"Khóa API không hợp lệ hoặc không có quyền ({s})", status=s)
        if s == 429:
            ra = r.headers.get("retry-after")
            try:
                ra_s = float(ra) if ra else None
            except ValueError:
                ra_s = None
            return LLMError("rate_limit", "Vượt giới hạn tốc độ (429)", retryable=True, retry_after_s=ra_s, status=s)
        if s >= 500:
            return LLMError("server", f"Lỗi máy chủ ({s})", retryable=True, status=s)
        if s == 404:
            return LLMError("bad_request", f"Không thấy endpoint/model (404) — kiểm tra base URL và model. {detail}", status=s)
        return LLMError("bad_request", f"Yêu cầu bị từ chối ({s}) {detail}", status=s)

    def _read_json(self, j: dict, t0: float, messages) -> LLMResponse:
        text = j["choices"][0]["message"]["content"] or ""
        total = int((time.perf_counter() - t0) * 1000)
        usage = j.get("usage") or {}
        est = not usage
        return LLMResponse(
            text=text.strip(), model=j.get("model", self.model),
            input_tokens=int(usage.get("prompt_tokens") or estimate_tokens("".join(m["content"] for m in messages))),
            output_tokens=int(usage.get("completion_tokens") or estimate_tokens(text)),
            usage_estimated=est, ttft_ms=total, total_ms=total,
        )

    def _read_stream(self, r: httpx.Response, t0: float, messages) -> LLMResponse:
        parts: list[str] = []
        ttft = None
        usage: dict = {}
        model = self.model
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            model = chunk.get("model", model)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices") or []:
                delta = (ch.get("delta") or {}).get("content")
                if delta:
                    if ttft is None:
                        ttft = int((time.perf_counter() - t0) * 1000)
                    parts.append(delta)
        text = "".join(parts).strip()
        total = int((time.perf_counter() - t0) * 1000)
        return LLMResponse(
            text=text, model=model,
            input_tokens=int(usage.get("prompt_tokens") or estimate_tokens("".join(m["content"] for m in messages))),
            output_tokens=int(usage.get("completion_tokens") or estimate_tokens(text)),
            usage_estimated=not usage, ttft_ms=ttft if ttft is not None else total, total_ms=total,
        )


def make_provider(cfg, api_key: str | None):
    return AgnesAdapter(cfg.api_base_url, api_key, cfg.api_model, cfg.api_stream, cfg.api_extra_body)
