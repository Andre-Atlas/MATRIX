"""AI Gateway Router & Orchestrator (MATRIX Gateway Layer 2).

Manages:
- OpenAI-compatible chat completion routing
- Exact and semantic prompt cache lookup (x-matrix-cache: HIT / MISS)
- Provider cascading fallback (Primary -> Secondary -> Fallback)
- Per-provider Circuit Breaker protection
- RPM and TPM rate limiting per project
- Telemetry, latency statistics, and request audit logging
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from matrix_server.gateway.cache import GatewayCache
from matrix_server.gateway.circuit_breaker import CircuitBreaker
from matrix_server.gateway.rate_limiter import GatewayRateLimiter
from matrix_server.pricing import compute_cost


class GatewayError(Exception):
    """Base exception for Gateway operations."""


class AllProvidersUnavailableError(GatewayError):
    """Raised when all providers in the cascading fallback chain fail or have OPEN circuit breakers."""


class ProviderExecutionError(GatewayError):
    """Raised when a specific provider fails during request execution."""


@dataclass
class BaseProvider:
    name: str
    circuit_breaker: CircuitBreaker = field(init=False)
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "circuit_breaker": self.circuit_breaker.state,
            "failure_count": self.circuit_breaker.failure_count,
            "avg_latency_ms": self.avg_latency_ms,
            "total_requests": self._total_requests,
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
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    timeout: float = 30.0
    mock_fallback_on_no_key: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.api_key:
            self.api_key = os.environ.get("OPENAI_API_KEY")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._total_requests += 1

        if not self.api_key:
            if self.mock_fallback_on_no_key:
                # Provide simulated OpenAI response when no live key is present
                prompt_text = " ".join(str(m.get("content", "")) for m in messages)
                p_tokens = max(1, len(prompt_text.split()))
                content = f"Simulated OpenAI ({model}) response."
                c_tokens = max(1, len(content.split()))
                self._successful_requests += 1
                return {
                    "id": f"chatcmpl-oai-{uuid.uuid4().hex[:12]}",
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
                        "total_tokens": p_tokens + c_tokens,
                    },
                }
            self._failed_requests += 1
            raise ProviderExecutionError(f"{self.name}: OPENAI_API_KEY not configured")

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                self._successful_requests += 1
                return data
        except Exception as exc:
            self._failed_requests += 1
            raise ProviderExecutionError(f"{self.name} HTTP error: {exc}") from exc


@dataclass
class AnthropicProvider(BaseProvider):
    name: str = "anthropic"
    api_key: str | None = None
    base_url: str = "https://api.anthropic.com/v1"
    timeout: float = 30.0
    mock_fallback_on_no_key: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.api_key:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._total_requests += 1

        if not self.api_key:
            if self.mock_fallback_on_no_key:
                prompt_text = " ".join(str(m.get("content", "")) for m in messages)
                p_tokens = max(1, len(prompt_text.split()))
                content = f"Simulated Anthropic ({model}) response."
                c_tokens = max(1, len(content.split()))
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
                                "content": content,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": p_tokens,
                        "completion_tokens": c_tokens,
                        "total_tokens": p_tokens + c_tokens,
                    },
                }
            self._failed_requests += 1
            raise ProviderExecutionError(f"{self.name}: ANTHROPIC_API_KEY not configured")

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

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/messages", json=payload, headers=headers)
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
            raise ProviderExecutionError(f"{self.name} HTTP error: {exc}") from exc


@dataclass
class GatewayRequestLog:
    id: str
    timestamp: str
    project_id: str
    model: str
    provider: str
    status: Literal["CACHE_HIT", "DIRECT", "FALLBACK"]
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "project_id": self.project_id,
            "model": self.model,
            "provider": self.provider,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 2),
            "tokens": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            },
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class GatewayRouter:
    cache: GatewayCache = field(default_factory=GatewayCache)
    rate_limiter: GatewayRateLimiter = field(default_factory=GatewayRateLimiter)
    providers: dict[str, BaseProvider] = field(default_factory=dict)
    routing_order: list[str] = field(default_factory=lambda: ["openai", "anthropic", "mock"])

    _logs: deque[GatewayRequestLog] = field(default_factory=lambda: deque(maxlen=1000))
    _latencies: deque[float] = field(default_factory=lambda: deque(maxlen=1000))

    def __post_init__(self) -> None:
        if not self.providers:
            self.providers = {
                "openai": OpenAIProvider(name="openai"),
                "anthropic": AnthropicProvider(name="anthropic"),
                "mock": MockProvider(name="mock"),
            }

    def register_provider(self, provider: BaseProvider) -> None:
        """Registers or replaces a provider in the gateway."""
        self.providers[provider.name] = provider
        if provider.name not in self.routing_order:
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
        **kwargs: Any,
    ) -> tuple[dict[str, Any], Literal["HIT", "MISS"], str]:
        """Routes a chat completion request through cache, rate limiter, and cascading providers.

        Returns: (completion_response, cache_status, provider_used)
        """
        # 1. Rate Limiting Check
        est_tokens = sum(len(str(m.get("content", "")).split()) * 2 for m in messages)
        allowed, reason = self.rate_limiter.check_rate_limit(project_id, estimated_tokens=est_tokens)
        if not allowed:
            from fastapi import HTTPException
            raise HTTPException(status_code=429, detail=reason or "Rate limit exceeded")

        req_start = time.monotonic()

        # 2. Cache Lookup
        cached = self.cache.get(model=model, messages=messages, temperature=temperature)
        if cached is not None:
            latency_ms = max(0.1, (time.monotonic() - req_start) * 1000.0)
            self._latencies.append(latency_ms)

            usage = cached.get("usage", {})
            p_tokens = usage.get("prompt_tokens", 0)
            c_tokens = usage.get("completion_tokens", 0)
            tot_tokens = usage.get("total_tokens", p_tokens + c_tokens)
            cost = compute_cost(model, input_tokens=p_tokens, output_tokens=c_tokens)

            # Record cached request in rate limiter (counts against RPM, zero new tokens)
            self.rate_limiter.record_request(project_id, tokens=0)

            # Log cache hit
            self._record_log(
                project_id=project_id,
                model=model,
                provider="cache",
                status="CACHE_HIT",
                latency_ms=latency_ms,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=tot_tokens,
                cost_usd=cost,
            )
            return cached, "HIT", "cache"

        # 3. Provider Dispatch with Cascading Fallback
        errors: list[str] = []
        attempt = 0

        for provider_name in self.routing_order:
            provider = self.providers.get(provider_name)
            if provider is None:
                continue

            # Circuit breaker check: is the provider healthy?
            if not provider.circuit_breaker.can_execute():
                errors.append(
                    f"{provider_name}: Circuit breaker is {provider.circuit_breaker.state} (blocked)"
                )
                continue

            provider_start = time.monotonic()
            try:
                # Attempt call through provider
                response = await provider.complete(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )

                # Provider success
                call_latency_ms = (time.monotonic() - provider_start) * 1000.0
                total_latency_ms = (time.monotonic() - req_start) * 1000.0

                provider.record_latency(call_latency_ms)
                provider.circuit_breaker.record_success()
                self._latencies.append(total_latency_ms)

                # Extract tokens & calculate cost
                usage = response.get("usage", {})
                p_tokens = usage.get("prompt_tokens", 0)
                c_tokens = usage.get("completion_tokens", 0)
                tot_tokens = usage.get("total_tokens", p_tokens + c_tokens)
                cost = compute_cost(model, input_tokens=p_tokens, output_tokens=c_tokens)

                # Store in cache
                self.cache.set(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    response=response,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                )

                # Record usage in rate limiter
                self.rate_limiter.record_request(project_id, tokens=tot_tokens)

                # Status is DIRECT if first attempted provider succeeded, else FALLBACK
                status: Literal["DIRECT", "FALLBACK"] = "DIRECT" if attempt == 0 else "FALLBACK"

                self._record_log(
                    project_id=project_id,
                    model=model,
                    provider=provider_name,
                    status=status,
                    latency_ms=total_latency_ms,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=tot_tokens,
                    cost_usd=cost,
                )

                return response, "MISS", provider_name

            except Exception as exc:  # noqa: BLE001 - resilience layer catches provider failure to trigger fallback
                provider.circuit_breaker.record_failure()
                errors.append(f"{provider_name}: {exc}")
                attempt += 1
                continue

        # All providers failed
        from fastapi import HTTPException
        err_msg = "All LLM providers unavailable: " + "; ".join(errors)
        raise HTTPException(status_code=503, detail=err_msg)

    def _record_log(
        self,
        project_id: str,
        model: str,
        provider: str,
        status: Literal["CACHE_HIT", "DIRECT", "FALLBACK"],
        latency_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
    ) -> None:
        log = GatewayRequestLog(
            id=f"gw-{uuid.uuid4().hex[:12]}",
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            project_id=project_id,
            model=model,
            provider=provider,
            status=status,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
        )
        self._logs.append(log)

    def get_stats(self) -> dict[str, Any]:
        """Returns aggregate gateway telemetry."""
        cache_stats = self.cache.stats()
        total_requests = len(self._logs)
        avg_latency = (
            round(sum(self._latencies) / len(self._latencies), 2)
            if self._latencies
            else 0.0
        )
        return {
            "total_requests": total_requests,
            "cache_hit_rate": cache_stats["hit_rate"],
            "tokens_saved": cache_stats["tokens_saved"],
            "cost_saved_usd": cache_stats["cost_saved_usd"],
            "avg_latency_ms": avg_latency,
        }

    def get_providers_status(self) -> list[dict[str, Any]]:
        """Returns health and performance status of all registered providers."""
        return [provider.to_dict() for provider in self.providers.values()]

    def get_logs(self, limit: int = 50, project_id: str | None = None) -> list[dict[str, Any]]:
        """Returns the most recent request audit logs."""
        all_logs = list(self._logs)
        if project_id:
            all_logs = [log for log in all_logs if log.project_id == project_id]
        # Return newest first
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


# Global router instance for default injection
_default_router: GatewayRouter | None = None


def get_default_gateway_router() -> GatewayRouter:
    global _default_router
    if _default_router is None:
        _default_router = GatewayRouter()
    return _default_router
