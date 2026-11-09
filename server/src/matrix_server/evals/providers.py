"""Model providers and embedders used by experiments and LLM judges.

Security rules (calls to external APIs):
- API keys are read from the environment at call time and never stored on objects.
- Errors raise `ProviderError` with a sanitized message: no headers, no key, and at most
  200 chars of the provider's error text (with anything that looks like a key redacted).
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from matrix_server.pricing import compute_cost

__all__ = [
    "AnthropicProvider",
    "Completion",
    "Embedder",
    "FakeProvider",
    "HashingEmbedder",
    "LLMProvider",
    "OpenAICompatibleProvider",
    "OpenAIEmbedder",
    "ProviderError",
    "get_provider",
    "register_provider",
]

OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
_MAX_ERROR_CHARS = 200
_KEY_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-*.]{4,}"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)(api[_-]?key[\"'=:\s]+)\S+"),
)


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    model: str
    raw: dict | None = None


class ProviderError(RuntimeError):
    """Sanitized provider failure. Never carries keys, headers or full response bodies."""

    def __init__(self, status: int | None, provider: str, message: str = "") -> None:
        self.status = status
        self.provider = provider
        self.message = message
        super().__init__(f"{provider} request failed (status {status if status is not None else 'n/a'})"
                         + (f": {message}" if message else ""))


@runtime_checkable
class LLMProvider(Protocol):
    def complete(self, messages: list[dict], model: str, params: dict | None = None) -> Completion: ...


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


# --------------------------------------------------------------------------- helpers


def _sanitize(text: str, key: str | None) -> str:
    out = str(text or "")
    if key:
        out = out.replace(key, "[REDACTED]")
    for rx in _KEY_PATTERNS:
        out = rx.sub(lambda m: (m.group(1) if m.re.groups else "") + "[REDACTED]", out)
    out = " ".join(out.split())
    return out[:_MAX_ERROR_CHARS]


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:1000]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
        if isinstance(err, str):
            return err
        if isinstance(data.get("message"), str):
            return data["message"]
    return str(data)[:1000]


def _read_key(env_name: str | None) -> str | None:
    if not env_name:
        return None
    val = os.environ.get(env_name)
    return val.strip() if val and val.strip() else None


def _content_text(content: Any) -> str:
    """Flatten OpenAI/Anthropic style content (str or list of parts) into text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return "\n".join(parts)
    return str(content)


class _HTTPBase:
    provider_name = "http"

    def __init__(
        self,
        api_key_env: str | None,
        timeout: float = 60,
        max_retries: int = 2,
        backoff: float = 0.5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.max_retries = max(int(max_retries), 0)
        self.backoff = backoff
        self._transport = transport
        self._sleep = sleep

    def _post(self, url: str, headers: dict, payload: dict, key: str | None) -> dict:
        attempt = 0
        while True:
            try:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    resp = client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries and isinstance(exc, httpx.TransportError):
                    self._wait(attempt, None)
                    attempt += 1
                    continue
                raise ProviderError(None, self.provider_name, f"network error: {type(exc).__name__}") from None
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.max_retries:
                self._wait(attempt, resp.headers.get("retry-after"))
                attempt += 1
                continue
            if resp.status_code >= 400:
                raise ProviderError(resp.status_code, self.provider_name, _sanitize(_error_text(resp), key))
            try:
                data = resp.json()
            except ValueError:
                raise ProviderError(resp.status_code, self.provider_name, "invalid JSON response") from None
            if not isinstance(data, dict):
                raise ProviderError(resp.status_code, self.provider_name, "unexpected response shape")
            return data

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff * (2**attempt)
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 10.0))
            except ValueError:
                pass
        self._sleep(delay)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(api_key_env={self.api_key_env!r})"


# --------------------------------------------------------------------------- OpenAI


class OpenAICompatibleProvider(_HTTPBase):
    """OpenAI Chat Completions API and compatibles (vLLM, Ollama, ...) via `base_url`.

    With a custom `base_url` the key is optional (local servers); with the default
    OpenAI URL a missing key raises `ProviderError`.
    """

    provider_name = "openai"

    def __init__(
        self,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout: float = 60,
        **kw: Any,
    ) -> None:
        super().__init__(api_key_env, timeout, **kw)
        self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
        self._custom_url = base_url is not None

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(base_url={self.base_url!r}, api_key_env={self.api_key_env!r})"

    def _headers(self) -> tuple[dict, str | None]:
        key = _read_key(self.api_key_env)
        if key is None and not self._custom_url:
            raise ProviderError(None, self.provider_name, f"missing API key (set {self.api_key_env})")
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        return headers, key

    def complete(self, messages: list[dict], model: str, params: dict | None = None) -> Completion:
        headers, key = self._headers()
        payload: dict[str, Any] = {"model": model, "messages": messages}
        payload.update(params or {})
        t0 = time.perf_counter()
        data = self._post(f"{self.base_url}/chat/completions", headers, payload, key)
        latency = (time.perf_counter() - t0) * 1000
        try:
            choice = data["choices"][0]
            text = _content_text(choice["message"].get("content"))
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError(200, self.provider_name, "unexpected response shape") from None
        usage = data.get("usage") or {}
        inp = int(usage.get("prompt_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        resolved = str(data.get("model") or model)
        return Completion(
            text=text,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=round(latency, 3),
            cost_usd=compute_cost(resolved, inp, out, cached),
            model=resolved,
            raw=data,
        )


# --------------------------------------------------------------------------- Anthropic


class AnthropicProvider(_HTTPBase):
    """Anthropic Messages API. System messages are moved to the top-level `system` field."""

    provider_name = "anthropic"
    default_max_tokens = 1024

    def __init__(self, api_key_env: str = "ANTHROPIC_API_KEY", timeout: float = 60, **kw: Any) -> None:
        super().__init__(api_key_env, timeout, **kw)
        self.url = ANTHROPIC_URL

    def complete(self, messages: list[dict], model: str, params: dict | None = None) -> Completion:
        key = _read_key(self.api_key_env)
        if key is None:
            raise ProviderError(None, self.provider_name, f"missing API key (set {self.api_key_env})")
        headers = {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        system_parts: list[str] = []
        msgs: list[dict] = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system_parts.append(_content_text(m.get("content")))
            else:
                msgs.append({"role": "assistant" if role == "assistant" else "user",
                             "content": m.get("content") if m.get("content") is not None else ""})
        p = dict(params or {})
        # OpenAI-only knobs the Messages API rejects.
        for k in ("logprobs", "top_logprobs", "n", "response_format", "seed",
                  "frequency_penalty", "presence_penalty"):
            p.pop(k, None)
        if "stop" in p:
            stop = p.pop("stop")
            p["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop or [])
        if "max_completion_tokens" in p:
            p.setdefault("max_tokens", p.pop("max_completion_tokens"))
        payload: dict[str, Any] = {"model": model, "messages": msgs,
                                   "max_tokens": p.pop("max_tokens", self.default_max_tokens)}
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        payload.update(p)
        t0 = time.perf_counter()
        data = self._post(self.url, headers, payload, key)
        latency = (time.perf_counter() - t0) * 1000
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ProviderError(200, self.provider_name, "unexpected response shape")
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        usage = data.get("usage") or {}
        base_in = int(usage.get("input_tokens") or 0)
        cr = int(usage.get("cache_read_input_tokens") or 0)
        cw = int(usage.get("cache_creation_input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        resolved = str(data.get("model") or model)
        return Completion(
            text=text,
            input_tokens=base_in + cr + cw,
            output_tokens=out,
            latency_ms=round(latency, 3),
            # compute_cost expects cache reads inside input_tokens and cache writes separately.
            cost_usd=compute_cost(resolved, base_in + cr, out, cr, cw),
            model=resolved,
            raw=data,
        )


# --------------------------------------------------------------------------- Fake


def _stable_hash(s: str) -> int:
    return int.from_bytes(hashlib.sha256(s.encode("utf-8")).digest()[:8], "big")


_FAKE_PREFIXES = ("Answer", "Response", "Reply", "Result", "Output", "Solution", "Verdict", "Outcome")


class FakeProvider:
    """Deterministic offline provider for tests and demos (no network, no key).

    Output = "<Prefix>: <last user message>", where the prefix depends on (seed, model),
    so two models usually differ. Tokens are word counts; latency is derived from the hash.
    """

    provider_name = "fake"

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def __repr__(self) -> str:
        return f"FakeProvider(seed={self.seed})"

    def complete(self, messages: list[dict], model: str, params: dict | None = None) -> Completion:
        last_user = ""
        for m in reversed(messages):
            if m.get("role", "user") == "user":
                last_user = _content_text(m.get("content"))
                break
        h = _stable_hash(f"{self.seed}:{model}")
        text = f"{_FAKE_PREFIXES[h % len(_FAKE_PREFIXES)]}: {last_user.strip()}"
        inp = sum(len(_content_text(m.get("content")).split()) for m in messages)
        out = len(text.split())
        latency = float(20 + (h >> 8) % 180 + out * 2)
        return Completion(
            text=text,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency,
            cost_usd=compute_cost(model, inp, out),
            model=model,
            raw=None,
        )


# --------------------------------------------------------------------------- registry

_PROVIDERS: dict[str, Callable[..., LLMProvider]] = {
    "openai": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "fake": FakeProvider,
}


def register_provider(name: str, factory: Callable[..., LLMProvider]) -> None:
    _PROVIDERS[name.strip().lower()] = factory


def get_provider(name: str, **opts: Any) -> LLMProvider:
    key = (name or "").strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(f"unknown provider {name!r}; available: {sorted(_PROVIDERS)}")
    return _PROVIDERS[key](**opts)


# --------------------------------------------------------------------------- embedders


class OpenAIEmbedder(_HTTPBase):
    provider_name = "openai"

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        timeout: float = 60,
        **kw: Any,
    ) -> None:
        super().__init__(api_key_env, timeout, **kw)
        self.model = model
        self.base_url = (base_url or OPENAI_BASE_URL).rstrip("/")
        self._custom_url = base_url is not None

    def __repr__(self) -> str:
        return f"OpenAIEmbedder(model={self.model!r}, base_url={self.base_url!r}, api_key_env={self.api_key_env!r})"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        key = _read_key(self.api_key_env)
        if key is None and not self._custom_url:
            raise ProviderError(None, self.provider_name, f"missing API key (set {self.api_key_env})")
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        data = self._post(f"{self.base_url}/embeddings", headers, {"model": self.model, "input": list(texts)}, key)
        try:
            rows = sorted(data["data"], key=lambda r: r.get("index", 0))
            vecs = [[float(x) for x in r["embedding"]] for r in rows]
        except (KeyError, TypeError, ValueError):
            raise ProviderError(200, self.provider_name, "unexpected response shape") from None
        if len(vecs) != len(texts):
            raise ProviderError(200, self.provider_name, "embedding count mismatch")
        return vecs


_TOKEN_RX = re.compile(r"\w+", re.UNICODE)


class HashingEmbedder:
    """Offline bag-of-words embedder (signed feature hashing, L2-normalized).

    Deterministic across processes; captures lexical overlap only. For tests/demo.
    """

    def __init__(self, dim: int = 256) -> None:
        if dim < 1:
            raise ValueError("dim must be >= 1")
        self.dim = dim

    def __repr__(self) -> str:
        return f"HashingEmbedder(dim={self.dim})"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in _TOKEN_RX.findall((t or "").lower()):
                h = int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(), "big")
                vec[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec] if norm > 0 else vec)
        return out
