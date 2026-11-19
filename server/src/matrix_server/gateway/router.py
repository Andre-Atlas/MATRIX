"""AI Gateway Router & Orchestrator (MATRIX Gateway Layer 2 / T8).

Manages:
- OpenAI-compatible chat completion routing
- Strict no-silent-mock policy: missing provider API keys return 503 provider_not_configured;
  mock provider runs only when explicitly requested (`provider="mock"`), tagged `simulated=True`
- Per-project isolation for cache, rate limits, stats, providers, rules, and audit logs
- Shared DB persistence (`gateway_rate_windows`, `gateway_requests`) with bounded retention cleanup
- Per-provider parameter allowlist with `x-matrix-warnings` and configurable caps for `n` & `max_tokens`
- Provider cascading fallback and per-provider circuit breaker protection
- Sanitized error reporting (no secrets, no full URLs, max 200 chars of provider body)
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.gateway.cache import GatewayCache
from matrix_server.gateway.circuit_breaker import CircuitBreaker
from matrix_server.gateway.models import GatewayRequest
from matrix_server.gateway.rate_limiter import GatewayRateLimiter
from matrix_server.pricing import compute_cost
from matrix_server.storage.db import as_utc

OPENAI_ALLOWED_PARAMS: frozenset[str] = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "tools",
        "tool_choice",
        "response_format",
        "presence_penalty",
        "frequency_penalty",
        "n",
        "seed",
        "user",
    }
)

ANTHROPIC_ALLOWED_PARAMS: frozenset[str] = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "tools",
        "tool_choice",
    }
)

MOCK_ALLOWED_PARAMS: frozenset[str] = OPENAI_ALLOWED_PARAMS

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)[^\s'\"]+", re.IGNORECASE)
_API_KEY_HEADER_RE = re.compile(r"(x-api-key\s*[:=]\s*)[^\s'\",;]+", re.IGNORECASE)
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def sanitize_provider_error(
    exc: Exception | str,
    *,
    secret_values: Iterable[str] = (),
    max_body_chars: int = 200,
) -> str:
    """Sanitizes provider errors: strips secrets, redacts URLs, and bounds provider body length."""
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raw_body = ""
        if exc.response is not None:
            try:
                raw_body = exc.response.text or ""
            except Exception:  # noqa: BLE001
                raw_body = ""
        cleaned_body = _redact_text(raw_body, secret_values=secret_values)
        if len(cleaned_body) > max_body_chars:
            cleaned_body = cleaned_body[:max_body_chars] + "..."
        msg = f"HTTP {status_code}: {cleaned_body}" if cleaned_body else f"HTTP {status_code}"
    else:
        raw_msg = str(exc)
        msg = _redact_text(raw_msg, secret_values=secret_values)
        if len(msg) > max_body_chars + 100:
            msg = msg[: max_body_chars + 100] + "..."
    return msg.strip()


def _redact_text(text: str, *, secret_values: Iterable[str] = ()) -> str:
    cleaned = text
    for secret in secret_values:
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[redacted-secret]")
    cleaned = _BEARER_RE.sub(r"\1[redacted-secret]", cleaned)
    cleaned = _API_KEY_HEADER_RE.sub(r"\1[redacted-secret]", cleaned)
    cleaned = _SK_KEY_RE.sub("[redacted-secret]", cleaned)
    cleaned = _URL_RE.sub("[redacted-url]", cleaned)
    return cleaned


class GatewayError(Exception):
    """Base exception for Gateway operations."""


class AllProvidersUnavailableError(GatewayError):
    """Raised when all providers in the cascading fallback chain fail or have OPEN circuit breakers."""


class ProviderExecutionError(GatewayError):
    """Raised when a specific provider fails during request execution."""


class ProviderNotConfiguredError(GatewayError):
    """Raised when a provider's required operator API key environment variable is not set."""

    def __init__(self, provider: str, env_var: str) -> None:
        self.provider = provider
        self.env_var = env_var
        super().__init__(f"{provider}: {env_var} not configured")


def filter_provider_params(
    allowed_params: frozenset[str],
    extra_params: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Filters extra request parameters against a provider allowlist.

    Returns `(filtered_params, warnings)` where `warnings` lists every dropped parameter.
    """
    filtered: dict[str, Any] = {}
    warnings: list[str] = []
    for key in sorted(extra_params.keys()):
        val = extra_params[key]
        if key in allowed_params:
            filtered[key] = val
        else:
            warnings.append(f"dropped_param:{key}")
    return filtered, warnings


@dataclass
class BaseProvider:
    name: str
    circuit_breaker: CircuitBreaker = field(init=False)
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    is_simulated: bool = False
    allowed_params: frozenset[str] = OPENAI_ALLOWED_PARAMS

    _latencies: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    _total_requests: int = 0
    _successful_requests: int = 0
    _failed_requests: int = 0

    def __post_init__(self) -> None:
        self.circuit_breaker = CircuitBreaker(
            name=self.name,
            failure_threshold=self.failure_threshold,
            cooldown_seconds=self.cooldown_seconds,
        )

    @property
    def avg_latency_ms(self) -> float:
        if not self._latencies:
            return 0.0
        return round(sum(self._latencies) / len(self._latencies), 2)

    @property
    def total_requests(self) -> int:
        return self._total_requests

    def record_latency(self, latency_ms: float) -> None:
        self._latencies.append(latency_ms)

    def to_dict(
        self,
        *,
        total_requests: int | None = None,
        avg_latency_ms: float | None = None,
    ) -> dict[str, Any]:
        if self.name == "openai":
            models = ["gpt-4o", "gpt-4o-mini", "o3-mini"]
        elif self.name == "anthropic":
            models = ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"]
        else:
            models = ["mock-gpt-4o", "mock-fast"]
        return {
            "name": self.name,
            "circuit_breaker": self.circuit_breaker.state,
            "failure_count": self.circuit_breaker.failure_count,
            "failure_threshold": self.failure_threshold,
            "avg_latency_ms": self.avg_latency_ms if avg_latency_ms is None else round(avg_latency_ms, 2),
            "total_requests": self._total_requests if total_requests is None else total_requests,
            "models": models,
        }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError


@dataclass
class MockProvider(BaseProvider):
    name: str = "mock"
    should_fail: bool = False
    fail_error_message: str = "Mock provider forced failure"
    simulated_latency_ms: float = 2.0
    mock_content: str | None = None
    is_simulated: bool = True
    allowed_params: frozenset[str] = MOCK_ALLOWED_PARAMS

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._total_requests += 1

        if self.simulated_latency_ms > 0:
            await asyncio.sleep(self.simulated_latency_ms / 1000.0)

        if self.should_fail:
            self._failed_requests += 1
            raise ProviderExecutionError(f"{self.name}: {self.fail_error_message}")

        # Compute synthetic tokens
        prompt_text = " ".join(str(m.get("content", "")) for m in messages)
        p_tokens = max(1, len(prompt_text.split()))
        content = self.mock_content or f"Mock gateway completion for {model}"
        c_tokens = max(1, len(content.split()))
        tot_tokens = p_tokens + c_tokens

        self._successful_requests += 1
        return {
            "id": f"chatcmpl-mock-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": tot_tokens,
            },
        }


@dataclass
class OpenAIProvider(BaseProvider):
    name: str = "openai"
    api_key_env: str = "OPENAI_API_KEY"
    base_url_env: str = "OPENAI_BASE_URL"
    default_base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = 30.0
    is_simulated: bool = False
    allowed_params: frozenset[str] = OPENAI_ALLOWED_PARAMS

    def _resolve_api_key(self) -> str | None:
        return self.api_key or os.environ.get(self.api_key_env)

    def _resolve_base_url(self) -> str:
        return (self.base_url or os.environ.get(self.base_url_env) or self.default_base_url).rstrip("/")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._total_requests += 1
        resolved_key = self._resolve_api_key()
        if not resolved_key:
            self._failed_requests += 1
            raise ProviderNotConfiguredError(self.name, self.api_key_env)

        filtered_kwargs, _ = filter_provider_params(self.allowed_params, kwargs)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        for k, v in filtered_kwargs.items():
            if k not in ("model", "messages", "temperature", "max_tokens"):
                payload[k] = v

        headers = {
            "Authorization": f"Bearer {resolved_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._resolve_base_url()}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                self._successful_requests += 1
                return data
        except Exception as exc:
            self._failed_requests += 1
            sanitized = sanitize_provider_error(exc, secret_values=[resolved_key])
            raise ProviderExecutionError(f"{self.name} error: {sanitized}") from exc


@dataclass
class AnthropicProvider(BaseProvider):
    name: str = "anthropic"
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url_env: str = "ANTHROPIC_BASE_URL"
    default_base_url: str = "https://api.anthropic.com/v1"
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = 30.0
    is_simulated: bool = False
    allowed_params: frozenset[str] = ANTHROPIC_ALLOWED_PARAMS

    def _resolve_api_key(self) -> str | None:
        return self.api_key or os.environ.get(self.api_key_env)

    def _resolve_base_url(self) -> str:
        return (self.base_url or os.environ.get(self.base_url_env) or self.default_base_url).rstrip("/")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._total_requests += 1
        resolved_key = self._resolve_api_key()
        if not resolved_key:
            self._failed_requests += 1
            raise ProviderNotConfiguredError(self.name, self.api_key_env)

        filtered_kwargs, _ = filter_provider_params(self.allowed_params, kwargs)

        # Translate OpenAI messages format to Anthropic messages API
        system_content = ""
        anthropic_msgs: list[dict[str, str]] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_content = content
            else:
                anthropic_msgs.append({"role": role, "content": content})

        if not anthropic_msgs:
            anthropic_msgs.append({"role": "user", "content": "Hello"})

        anthropic_model = model if "claude" in model else "claude-3-5-sonnet-20241022"
        payload: dict[str, Any] = {
            "model": anthropic_model,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens or 1024,
            "temperature": temperature,
        }
        if system_content:
            payload["system"] = system_content
        for k in ("top_p", "tools", "tool_choice"):
            if k in filtered_kwargs:
                payload[k] = filtered_kwargs[k]
        if "stop" in filtered_kwargs:
            stop_val = filtered_kwargs["stop"]
            payload["stop_sequences"] = [stop_val] if isinstance(stop_val, str) else list(stop_val)

        headers = {
            "x-api-key": resolved_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        url = f"{self._resolve_base_url()}/messages"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                # Adapt to OpenAI ChatCompletion format
                extracted_text = ""
                for part in data.get("content", []):
                    if part.get("type") == "text":
                        extracted_text += part.get("text", "")

                input_tokens = data.get("usage", {}).get("input_tokens", 0)
                output_tokens = data.get("usage", {}).get("output_tokens", 0)

                self._successful_requests += 1
                return {
                    "id": f"chatcmpl-claude-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": extracted_text,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                    },
                }
        except Exception as exc:
            self._failed_requests += 1
            sanitized = sanitize_provider_error(exc, secret_values=[resolved_key])
            raise ProviderExecutionError(f"{self.name} error: {sanitized}") from exc


@dataclass
class GatewayRequestLog:
    id: str
    timestamp: str
    project_id: str
    model: str
    provider: str
    status: Literal["CACHE_HIT", "DIRECT", "FALLBACK", "ERROR"]
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    simulated: bool = False
    route: str = "/api/v1/gateway/chat/completions"
    error_sanitized: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.id,
            "timestamp": self.timestamp,
            "project_id": self.project_id,
            "route": self.route,
            "model": self.model,
            "provider": self.provider,
            "status": self.status,
            "status_code": 503 if self.status == "ERROR" else 200,
            "latency_ms": round(self.latency_ms, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tokens": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
            },
            "cost_usd": round(self.cost_usd, 6),
            "saved_cost_usd": round(self.cost_usd, 6) if self.status == "CACHE_HIT" else 0.0,
            "cache_hit": self.status == "CACHE_HIT",
            "fallback_triggered": self.status == "FALLBACK",
            "simulated": self.simulated,
            "error_sanitized": self.error_sanitized,
        }


def _row_to_log_dict(row: GatewayRequest) -> dict[str, Any]:
    utc_ts = as_utc(row.ts)
    ts_iso = utc_ts.isoformat().replace("+00:00", "Z") if utc_ts else ""
    tot = row.input_tokens + row.output_tokens
    return {
        "id": row.id,
        "request_id": row.id,
        "timestamp": ts_iso,
        "project_id": row.project_id,
        "route": row.route,
        "model": row.model,
        "provider": row.provider,
        "status": row.status,
        "status_code": 503 if row.status == "ERROR" else 200,
        "latency_ms": round(row.latency_ms, 2),
        "prompt_tokens": row.input_tokens,
        "completion_tokens": row.output_tokens,
        "total_tokens": tot,
        "tokens": {
            "prompt_tokens": row.input_tokens,
            "completion_tokens": row.output_tokens,
            "total_tokens": tot,
            "prompt": row.input_tokens,
            "completion": row.output_tokens,
            "total": tot,
        },
        "cost_usd": round(row.cost_usd, 6),
        "saved_cost_usd": round(row.cost_usd, 6) if row.cache_hit else 0.0,
        "cache_hit": row.cache_hit,
        "fallback_triggered": row.status == "FALLBACK",
        "simulated": row.simulated,
        "error_sanitized": row.error_sanitized,
    }


@dataclass
class GatewayRouter:
    cache: GatewayCache = field(default_factory=GatewayCache)
    rate_limiter: GatewayRateLimiter = field(default_factory=GatewayRateLimiter)
    providers: dict[str, BaseProvider] = field(default_factory=dict)
    # Default fallback chain contains ONLY real providers; mock runs ONLY when explicitly requested.
    routing_order: list[str] = field(default_factory=lambda: ["openai", "anthropic"])
    session_factory: sessionmaker[Session] | None = None

    max_tokens_cap: int = 8192
    max_n_cap: int = 4
    retention_days: int = 30
    cleanup_interval_requests: int = 100
    cleanup_batch_size: int = 500

    _logs: deque[GatewayRequestLog] = field(default_factory=lambda: deque(maxlen=1000))
    _latencies: deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    _write_counter: int = 0
    _cleanup_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.providers:
            self.providers = {
                "openai": OpenAIProvider(name="openai"),
                "anthropic": AnthropicProvider(name="anthropic"),
                "mock": MockProvider(name="mock"),
            }
        if self.session_factory is not None and self.rate_limiter.session_factory is None:
            self.rate_limiter.session_factory = self.session_factory

    def bind_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        """Attaches a SQLAlchemy session factory for shared DB persistence."""
        self.session_factory = session_factory
        self.rate_limiter.session_factory = session_factory

    def register_provider(self, provider: BaseProvider) -> None:
        """Registers or replaces a provider in the gateway."""
        self.providers[provider.name] = provider
        if provider.name not in self.routing_order and provider.name != "mock":
            self.routing_order.append(provider.name)

    def set_routing_order(self, order: list[str]) -> None:
        """Sets the cascading fallback order of provider names."""
        self.routing_order = list(order)

    async def route_chat_completion(
        self,
        project_id: str,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        provider: str | None = None,
        stream: bool = False,
        route: str = "/api/v1/gateway/chat/completions",
        **kwargs: Any,
    ) -> tuple[dict[str, Any], Literal["HIT", "MISS"], str]:
        """Routes a chat completion request through validation, rate limiter, cache, and providers.

        Returns: `(completion_response, cache_status, provider_used)`.
        `completion_response["x_matrix"]` includes `simulated`, `provider`, `cache`, and optional `warnings`.
        """
        # 0. Streaming & Caps validation
        if stream:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "stream_not_supported",
                    "message": "Streaming (SSE) is not supported yet.",
                },
            )

        if max_tokens is not None and max_tokens > self.max_tokens_cap:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "max_tokens_cap_exceeded",
                    "message": f"max_tokens ({max_tokens}) exceeds gateway cap of {self.max_tokens_cap}.",
                },
            )

        if "n" in kwargs and kwargs["n"] is not None:
            try:
                n_val = int(kwargs["n"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="Parameter 'n' must be an integer.") from exc
            if n_val < 1 or n_val > self.max_n_cap:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "n_cap_exceeded",
                        "message": f"Parameter 'n' ({n_val}) must be between 1 and {self.max_n_cap}.",
                    },
                )

        if provider is not None and provider not in self.providers:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "unknown_provider",
                    "message": f"Unknown provider '{provider}'.",
                },
            )

        # 1. Atomic Rate Limit Check & Reserve BEFORE calling provider
        est_tokens = sum(len(str(m.get("content", "")).split()) * 2 for m in messages)
        allowed, reason, window_start = self.rate_limiter.acquire(
            project_id,
            estimated_tokens=est_tokens,
        )
        if not allowed:
            raise HTTPException(status_code=429, detail=reason or "Rate limit exceeded")

        req_start = time.monotonic()
        cache_model_key = f"{provider}:{model}" if provider else model

        # 2. Per-project Cache Lookup
        cached = self.cache.get(
            model=cache_model_key,
            messages=messages,
            temperature=temperature,
            project_id=project_id,
        )
        if cached is not None:
            latency_ms = max(0.1, (time.monotonic() - req_start) * 1000.0)
            self._latencies.append(latency_ms)

            usage = cached.get("usage", {})
            p_tokens = usage.get("prompt_tokens", 0)
            c_tokens = usage.get("completion_tokens", 0)
            tot_tokens = usage.get("total_tokens", p_tokens + c_tokens)
            cost = compute_cost(model, input_tokens=p_tokens, output_tokens=c_tokens)

            # Reconcile tokens to 0 for cache hit (counts against RPM, zero new tokens)
            self.rate_limiter.reconcile_tokens(
                project_id=project_id,
                window_start=window_start,
                estimated_tokens=est_tokens,
                actual_tokens=0,
            )

            was_simulated = bool((cached.get("x_matrix") or {}).get("simulated", False))
            cached["x_matrix"] = {
                "simulated": was_simulated,
                "provider": "cache",
                "cache": "HIT",
            }

            self._record_log(
                project_id=project_id,
                route=route,
                model=model,
                provider="cache",
                status="CACHE_HIT",
                latency_ms=latency_ms,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=tot_tokens,
                cost_usd=cost,
                simulated=was_simulated,
            )
            return cached, "HIT", "cache"

        # 3. Provider Dispatch with Cascading Fallback (or explicit provider)
        candidate_order = [provider] if provider is not None else list(self.routing_order)
        not_configured_errors: list[ProviderNotConfiguredError] = []
        other_errors: list[str] = []
        attempt = 0

        for provider_name in candidate_order:
            prov = self.providers.get(provider_name)
            if prov is None:
                continue

            # Circuit breaker check
            if not prov.circuit_breaker.can_execute():
                other_errors.append(
                    f"{provider_name}: Circuit breaker is {prov.circuit_breaker.state} (blocked)"
                )
                continue

            filtered_kwargs, warnings = filter_provider_params(prov.allowed_params, kwargs)
            provider_start = time.monotonic()
            try:
                response = await prov.complete(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **filtered_kwargs,
                )

                call_latency_ms = (time.monotonic() - provider_start) * 1000.0
                total_latency_ms = (time.monotonic() - req_start) * 1000.0

                prov.record_latency(call_latency_ms)
                prov.circuit_breaker.record_success()
                self._latencies.append(total_latency_ms)

                usage = response.get("usage", {})
                p_tokens = int(usage.get("prompt_tokens", 0))
                c_tokens = int(usage.get("completion_tokens", 0))
                tot_tokens = int(usage.get("total_tokens", p_tokens + c_tokens))
                cost = compute_cost(model, input_tokens=p_tokens, output_tokens=c_tokens)

                is_simulated = bool(prov.is_simulated or provider_name == "mock")
                x_matrix: dict[str, Any] = {
                    "simulated": is_simulated,
                    "provider": provider_name,
                    "cache": "MISS",
                }
                if warnings:
                    x_matrix["warnings"] = warnings
                response["x_matrix"] = x_matrix

                # Store in project-isolated cache
                self.cache.set(
                    model=cache_model_key,
                    messages=messages,
                    temperature=temperature,
                    response=response,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    project_id=project_id,
                )

                # Reconcile actual tokens in shared rate-limit window
                self.rate_limiter.reconcile_tokens(
                    project_id=project_id,
                    window_start=window_start,
                    estimated_tokens=est_tokens,
                    actual_tokens=tot_tokens,
                )

                status: Literal["DIRECT", "FALLBACK"] = "DIRECT" if attempt == 0 else "FALLBACK"
                self._record_log(
                    project_id=project_id,
                    route=route,
                    model=model,
                    provider=provider_name,
                    status=status,
                    latency_ms=total_latency_ms,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=tot_tokens,
                    cost_usd=cost,
                    simulated=is_simulated,
                )

                return response, "MISS", provider_name

            except ProviderNotConfiguredError as exc:
                not_configured_errors.append(exc)
                attempt += 1
                continue
            except Exception as exc:  # noqa: BLE001 - resilience layer catches provider failure to trigger fallback
                prov.circuit_breaker.record_failure()
                sanitized = sanitize_provider_error(exc)
                other_errors.append(f"{provider_name}: {sanitized}")
                attempt += 1
                continue

        # All providers failed — release reserved estimated tokens (keep RPM count)
        self.rate_limiter.reconcile_tokens(
            project_id=project_id,
            window_start=window_start,
            estimated_tokens=est_tokens,
            actual_tokens=0,
        )
        total_latency_ms = (time.monotonic() - req_start) * 1000.0

        if not_configured_errors and not other_errors:
            missing_vars = [e.env_var for e in not_configured_errors]
            err_msg = "; ".join(str(e) for e in not_configured_errors)
            self._record_log(
                project_id=project_id,
                route=route,
                model=model,
                provider=provider or "none",
                status="ERROR",
                latency_ms=total_latency_ms,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cost_usd=0.0,
                simulated=False,
                error_sanitized=f"provider_not_configured: {err_msg}"[:500],
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "provider_not_configured",
                    "message": (
                        f"No configured LLM provider available ({err_msg}). "
                        "Set the required provider API key environment variable or explicitly request provider='mock'."
                    ),
                    "missing_env_vars": missing_vars,
                },
            )

        all_errs = [str(e) for e in not_configured_errors] + other_errors
        err_msg = "All LLM providers unavailable: " + "; ".join(all_errs)
        self._record_log(
            project_id=project_id,
            route=route,
            model=model,
            provider=provider or "none",
            status="ERROR",
            latency_ms=total_latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost_usd=0.0,
            simulated=False,
            error_sanitized=sanitize_provider_error(err_msg)[:500],
        )
        raise HTTPException(status_code=503, detail=err_msg)

    def _record_log(
        self,
        *,
        project_id: str,
        route: str,
        model: str,
        provider: str,
        status: Literal["CACHE_HIT", "DIRECT", "FALLBACK", "ERROR"],
        latency_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
        simulated: bool = False,
        error_sanitized: str | None = None,
    ) -> None:
        now_dt = datetime.now(UTC)
        log = GatewayRequestLog(
            id=f"gw-{uuid.uuid4().hex[:12]}",
            timestamp=now_dt.isoformat().replace("+00:00", "Z"),
            project_id=project_id,
            route=route,
            model=model,
            provider=provider,
            status=status,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            simulated=simulated,
            error_sanitized=error_sanitized,
        )
        self._logs.append(log)

        if self.session_factory is not None:
            with self.session_factory() as db:
                db.add(
                    GatewayRequest(
                        id=log.id,
                        project_id=project_id,
                        ts=now_dt,
                        route=route,
                        model=model,
                        provider=provider,
                        status=status,
                        latency_ms=latency_ms,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        cost_usd=cost_usd,
                        cache_hit=(status == "CACHE_HIT"),
                        simulated=simulated,
                        error_sanitized=error_sanitized,
                    )
                )
                db.commit()
            self._maybe_cleanup_requests()

    def _maybe_cleanup_requests(self) -> None:
        if self.session_factory is None:
            return
        with self._cleanup_lock:
            self._write_counter += 1
            should_clean = (self._write_counter % max(1, self.cleanup_interval_requests)) == 0
        if should_clean:
            self.cleanup_expired_requests()

    def cleanup_expired_requests(self) -> int:
        """Bounded opportunistic cleanup of `gateway_requests` older than `retention_days`."""
        if self.session_factory is None or self.retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        with self.session_factory() as db:
            stale_ids = db.scalars(
                select(GatewayRequest.id)
                .where(GatewayRequest.ts < cutoff)
                .limit(self.cleanup_batch_size)
            ).all()
            if not stale_ids:
                return 0
            res = db.execute(delete(GatewayRequest).where(GatewayRequest.id.in_(stale_ids)))
            db.commit()
            return int(res.rowcount or 0)

    def get_stats(self, project_id: str | None = None) -> dict[str, Any]:
        """Returns aggregate gateway telemetry, strictly scoped to `project_id` when provided."""
        active_providers = len(self.providers)
        healthy_providers = sum(
            1 for p in self.providers.values() if p.circuit_breaker.state != "OPEN"
        )

        if self.session_factory is not None:
            with self.session_factory() as db:
                stmt = select(GatewayRequest)
                if project_id is not None:
                    stmt = stmt.where(GatewayRequest.project_id == project_id)
                rows = db.scalars(stmt).all()

            total_requests = len(rows)
            cache_hits = sum(1 for r in rows if r.cache_hit)
            tokens_saved = sum(r.input_tokens + r.output_tokens for r in rows if r.cache_hit)
            cost_saved = round(sum(r.cost_usd for r in rows if r.cache_hit), 6)
            total_cost = round(sum(r.cost_usd for r in rows if not r.cache_hit and r.status != "ERROR"), 6)
            latencies = sorted(r.latency_ms for r in rows)
            fallback_events = sum(1 for r in rows if r.status == "FALLBACK")
            hit_rate = round(cache_hits / total_requests, 4) if total_requests > 0 else 0.0
        else:
            logs = [log for log in self._logs if project_id is None or log.project_id == project_id]
            total_requests = len(logs)
            cache_hits = sum(1 for log in logs if log.status == "CACHE_HIT")
            tokens_saved = sum(log.total_tokens for log in logs if log.status == "CACHE_HIT")
            cost_saved = round(sum(log.cost_usd for log in logs if log.status == "CACHE_HIT"), 6)
            total_cost = round(sum(log.cost_usd for log in logs if log.status not in ("CACHE_HIT", "ERROR")), 6)
            latencies = sorted(log.latency_ms for log in logs)
            fallback_events = sum(1 for log in logs if log.status == "FALLBACK")
            hit_rate = round(cache_hits / total_requests, 4) if total_requests > 0 else 0.0

        avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
        p50_latency = round(latencies[int(len(latencies) * 0.50)], 2) if latencies else 0.0
        p99_latency = (
            round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))], 2)
            if latencies
            else 0.0
        )

        return {
            "total_requests": total_requests,
            "cache_hits": cache_hits,
            "cache_hit_rate": hit_rate,
            "tokens_saved": tokens_saved,
            "cost_saved_usd": cost_saved,
            "saved_cost_usd": cost_saved,
            "total_cost_usd": total_cost,
            "avg_latency_ms": avg_latency,
            "latency_p50_ms": p50_latency,
            "latency_p99_ms": p99_latency,
            "active_providers": active_providers,
            "healthy_providers": healthy_providers,
            "fallback_events_24h": fallback_events,
        }

    def get_providers_status(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Returns health and per-project performance status of registered providers."""
        if project_id is None:
            return [provider.to_dict() for provider in self.providers.values()]

        # Compute per-project request counts and latencies so tenants never see each other's metrics
        counts: dict[str, int] = {}
        latency_sums: dict[str, float] = {}
        if self.session_factory is not None:
            with self.session_factory() as db:
                rows = db.scalars(
                    select(GatewayRequest).where(GatewayRequest.project_id == project_id)
                ).all()
            for r in rows:
                counts[r.provider] = counts.get(r.provider, 0) + 1
                latency_sums[r.provider] = latency_sums.get(r.provider, 0.0) + r.latency_ms
        else:
            for log in self._logs:
                if log.project_id == project_id:
                    counts[log.provider] = counts.get(log.provider, 0) + 1
                    latency_sums[log.provider] = latency_sums.get(log.provider, 0.0) + log.latency_ms

        result: list[dict[str, Any]] = []
        for provider in self.providers.values():
            c = counts.get(provider.name, 0)
            avg_lat = (latency_sums[provider.name] / c) if c > 0 else 0.0
            result.append(provider.to_dict(total_requests=c, avg_latency_ms=avg_lat))
        return result

    def get_rules(self, project_id: str | None = None) -> list[dict[str, Any]]:
        """Returns active gateway routing rules scoped to the project's rate limits."""
        rpm_limit = (
            self.rate_limiter.get_limits(project_id)[0]
            if project_id is not None
            else self.rate_limiter.default_rpm
        )
        return [
            {
                "id": "rule-tier-frontier",
                "name": "Frontier Production Cascade",
                "description": (
                    "Primary OpenAI GPT-4o routing with automatic Anthropic Claude 3.5 Sonnet "
                    "failover and project-isolated caching."
                ),
                "match_pattern": "gpt-4*",
                "primary_provider": "openai",
                "primary_model": "gpt-4o",
                "fallback_chain": ["anthropic/claude-3-5-sonnet-20241022"],
                "semantic_cache_enabled": self.cache.enable_semantic_cache,
                "cache_similarity_threshold": 0.95,
                "rate_limit_rpm": rpm_limit,
                "retry_count": 2,
                "timeout_ms": 30000,
                "is_active": True,
            },
            {
                "id": "rule-tier-claude",
                "name": "Anthropic Direct & Failover",
                "description": (
                    "Routes Claude model family requests to Anthropic with OpenAI GPT-4o "
                    "cascading fallback."
                ),
                "match_pattern": "claude-*",
                "primary_provider": "anthropic",
                "primary_model": "claude-3-5-sonnet-20241022",
                "fallback_chain": ["openai/gpt-4o"],
                "semantic_cache_enabled": self.cache.enable_semantic_cache,
                "cache_similarity_threshold": 0.95,
                "rate_limit_rpm": rpm_limit,
                "retry_count": 2,
                "timeout_ms": 30000,
                "is_active": True,
            },
        ]

    def get_logs(self, limit: int = 50, project_id: str | None = None) -> list[dict[str, Any]]:
        """Returns the most recent request audit logs (strictly filtered by project_id)."""
        if self.session_factory is not None:
            with self.session_factory() as db:
                stmt = select(GatewayRequest)
                if project_id is not None:
                    stmt = stmt.where(GatewayRequest.project_id == project_id)
                stmt = stmt.order_by(GatewayRequest.ts.desc()).limit(limit)
                rows = db.scalars(stmt).all()
            return [_row_to_log_dict(r) for r in rows]

        all_logs = list(self._logs)
        if project_id is not None:
            all_logs = [log for log in all_logs if log.project_id == project_id]
        all_logs.reverse()
        return [log.to_dict() for log in all_logs[:limit]]

    def reset(self) -> None:
        """Resets telemetry, cache, rate limits, and provider states (for testing)."""
        self.cache.clear()
        self.cache.reset_stats()
        self.rate_limiter.reset()
        self._logs.clear()
        self._latencies.clear()
        for p in self.providers.values():
            p.circuit_breaker.reset()
            p._latencies.clear()
            p._total_requests = 0
            p._successful_requests = 0
            p._failed_requests = 0
        if self.session_factory is not None:
            with self.session_factory() as db:
                db.execute(delete(GatewayRequest))
                db.commit()


# Global router instance for default injection
_default_router: GatewayRouter | None = None


def get_default_gateway_router() -> GatewayRouter:
    global _default_router
    if _default_router is None:
        _default_router = GatewayRouter()
    return _default_router
