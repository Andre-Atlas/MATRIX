"""Comprehensive Test Suite for MATRIX AI Gateway (Layer 2 Blueprint SOTA).

Covers:
- Circuit Breaker state transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
- Gateway Cache (SHA-256 hash, exact/semantic lookup, TTL, tokens & USD cost tracking)
- Rate Limiter (RPM & TPM per project with sliding window)
- Gateway Router (direct routing, caching, circuit breaker tripping, cascading fallback)
- API Endpoints & RBAC (chat completions, stats, providers, logs, headers)
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from rbac_helpers import add_membership, api_key_headers, make_user, session_token
from sqlalchemy import event, select

from matrix_server.app import create_app
from matrix_server.auth import Role, create_project
from matrix_server.config import Settings
from matrix_server.gateway.cache import GatewayCache, compute_cache_key
from matrix_server.gateway.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from matrix_server.gateway.models import GatewayRateWindow, GatewayRequest
from matrix_server.gateway.rate_limiter import GatewayRateLimiter
from matrix_server.gateway.router import (
    AnthropicProvider,
    GatewayRouter,
    MockProvider,
    OpenAIProvider,
    ProviderExecutionError,
    sanitize_provider_error,
)
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def db_factory(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'gateway_db_test.db'}")
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    init_db(eng)
    yield make_session_factory(eng)
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def app(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/gateway_test.db", span_store="memory")
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_setup(client):
    """An admin, a member and a viewer in project A, plus a member/viewer in isolated project B."""
    factory = client.app.state.session_factory
    with factory() as db:
        project, _ = create_project(db, "Test Project", with_key=False)
        other_project, _ = create_project(db, "Other Project", with_key=False)
        db.commit()
        project_id = project.id
        other_project_id = other_project.id
    tokens = {}
    for role in (Role.ADMIN, Role.MEMBER, Role.VIEWER):
        user_id = make_user(factory)
        add_membership(factory, user_id, project_id, role)
        tokens[role.label] = session_token(factory, user_id)
    other_member = make_user(factory)
    add_membership(factory, other_member, other_project_id, Role.MEMBER)
    other_token = session_token(factory, other_member)
    result = {
        "project_id": project_id,
        "admin_token": tokens["admin"],
        "member_token": tokens["member"],
        "viewer_token": tokens["viewer"],
        "key_headers": api_key_headers(factory, project_id),
        "other_project_id": other_project_id,
        "other_member_token": other_token,
    }
    return result


# ============================================================================
# 1. Circuit Breaker Unit Tests
# ============================================================================


def test_circuit_breaker_transitions():
    cb = CircuitBreaker(name="test_cb", failure_threshold=3, cooldown_seconds=0.1)

    assert cb.state == CircuitState.CLOSED.value
    assert cb.can_execute() is True
    assert cb.failure_count == 0

    # 1 failure -> still CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED.value
    assert cb.failure_count == 1
    assert cb.can_execute() is True

    # 2nd failure -> still CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED.value
    assert cb.failure_count == 2

    # 3rd failure -> trips to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN.value
    assert cb.failure_count == 3
    assert cb.can_execute() is False

    # Wait for cooldown to transition to HALF_OPEN
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN.value
    assert cb.can_execute() is True

    # Failure in HALF_OPEN trips immediately back to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN.value
    assert cb.can_execute() is False

    # Wait for cooldown again -> HALF_OPEN
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN.value

    # Success in HALF_OPEN closes the breaker
    cb.record_success()
    assert cb.state == CircuitState.CLOSED.value
    assert cb.failure_count == 0
    assert cb.can_execute() is True


@pytest.mark.asyncio
async def test_circuit_breaker_call_wrapper():
    cb = CircuitBreaker(name="call_test", failure_threshold=2, cooldown_seconds=0.1)

    async def sample_ok():
        return "success"

    async def sample_fail():
        raise RuntimeError("Service down")

    # Success call
    res = await cb.call(sample_ok)
    assert res == "success"
    assert cb.failure_count == 0

    # First failure
    with pytest.raises(RuntimeError):
        await cb.call(sample_fail)
    assert cb.failure_count == 1
    assert cb.state == CircuitState.CLOSED.value

    # Second failure trips breaker
    with pytest.raises(RuntimeError):
        await cb.call(sample_fail)
    assert cb.state == CircuitState.OPEN.value

    # Blocked by open circuit breaker
    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(sample_ok)


# ============================================================================
# 2. Gateway Cache Unit Tests
# ============================================================================


def test_gateway_cache_exact_hit_and_miss():
    cache = GatewayCache(default_ttl_seconds=3600)
    messages = [{"role": "user", "content": "What is the capital of France?"}]
    model = "gpt-4o"
    temp = 0.7

    # Miss on first attempt
    cached = cache.get(model=model, messages=messages, temperature=temp)
    assert cached is None
    assert cache.misses == 1
    assert cache.hits == 0

    # Store response with token usage
    response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "Paris."}}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
    }
    cache.set(
        model=model,
        messages=messages,
        temperature=temp,
        response=response,
        prompt_tokens=15,
        completion_tokens=5,
    )

    # Hit on second attempt
    cached_hit = cache.get(model=model, messages=messages, temperature=temp)
    assert cached_hit is not None
    assert cached_hit["choices"][0]["message"]["content"] == "Paris."
    assert cache.hits == 1
    assert cache.tokens_saved == 20
    assert cache.cost_saved_usd > 0.0

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == 0.5
    assert stats["tokens_saved"] == 20
    assert stats["cost_saved_usd"] > 0.0


def test_gateway_cache_ttl_expiration():
    cache = GatewayCache(default_ttl_seconds=1)
    messages = [{"role": "user", "content": "Hello"}]
    model = "gpt-4o"

    cache.set(
        model=model,
        messages=messages,
        temperature=0.7,
        response={"id": "test"},
        ttl_seconds=0,  # Expires immediately
    )

    # After expiry, should miss and evict
    cached = cache.get(model=model, messages=messages, temperature=0.7)
    assert cached is None


def test_gateway_cache_semantic_matching():
    cache = GatewayCache(enable_semantic_cache=True)
    model = "gpt-4o"

    orig_messages = [{"role": "user", "content": "Tell me a joke about robots."}]
    # Equivalent query with extra whitespace and casing
    sem_messages = [{"role": "user", "content": "  tell me a   joke about robots.  "}]

    cache.set(
        model=model,
        messages=orig_messages,
        temperature=0.0,
        response={"content": "Beep boop!"},
        prompt_tokens=10,
        completion_tokens=5,
    )

    # Should hit via semantic prompt normalization
    hit = cache.get(model=model, messages=sem_messages, temperature=0.0)
    assert hit is not None
    assert hit["content"] == "Beep boop!"
    assert cache.hits == 1


def test_cache_key_hash_consistency():
    key1 = compute_cache_key("gpt-4o", [{"role": "user", "content": "Hi"}], 0.7)
    key2 = compute_cache_key("GPT-4O", [{"role": "USER", "content": "Hi"}], 0.7)
    assert key1 == key2


# ============================================================================
# 3. Rate Limiter Unit Tests
# ============================================================================


def test_rate_limiter_rpm_and_tpm():
    limiter = GatewayRateLimiter(default_rpm=2, default_tpm=50, window_seconds=60.0)
    project_id = "proj-123"

    # 1. First request allowed
    allowed, reason = limiter.check_rate_limit(project_id, estimated_tokens=10)
    assert allowed is True
    assert reason is None
    limiter.record_request(project_id, tokens=10)

    # 2. Second request allowed
    allowed, reason = limiter.check_rate_limit(project_id, estimated_tokens=10)
    assert allowed is True
    limiter.record_request(project_id, tokens=10)

    # 3. Third request blocked by RPM limit (default_rpm=2)
    allowed, reason = limiter.check_rate_limit(project_id, estimated_tokens=10)
    assert allowed is False
    assert "RPM limit of 2 exceeded" in (reason or "")

    # Reset and test TPM limit
    limiter.reset()
    limiter.set_project_limits(project_id, rpm=100, tpm=30)

    limiter.record_request(project_id, tokens=25)
    # Asking for 10 more tokens exceeds 30
    allowed, reason = limiter.check_rate_limit(project_id, estimated_tokens=10)
    assert allowed is False
    assert "TPM limit of 30 exceeded" in (reason or "")


# ============================================================================
# 4. Gateway Router Unit Tests
# ============================================================================


@pytest.mark.asyncio
async def test_router_direct_and_cache_hit():
    primary_mock = MockProvider(name="mock_primary", mock_content="Direct primary response")
    router = GatewayRouter(
        providers={"mock_primary": primary_mock},
        routing_order=["mock_primary"],
    )

    messages = [{"role": "user", "content": "Direct test query"}]
    # 1. First execution: MISS, DIRECT
    resp, cache_status, provider = await router.route_chat_completion(
        project_id="proj_test",
        model="gpt-4o",
        messages=messages,
    )
    assert cache_status == "MISS"
    assert provider == "mock_primary"
    assert resp["choices"][0]["message"]["content"] == "Direct primary response"

    # 2. Second execution: HIT, cache
    resp2, cache_status2, provider2 = await router.route_chat_completion(
        project_id="proj_test",
        model="gpt-4o",
        messages=messages,
    )
    assert cache_status2 == "HIT"
    assert provider2 == "cache"
    assert resp2["choices"][0]["message"]["content"] == "Direct primary response"

    stats = router.get_stats()
    assert stats["total_requests"] == 2
    assert stats["cache_hit_rate"] == 0.5
    assert stats["tokens_saved"] > 0


@pytest.mark.asyncio
async def test_router_cascading_fallback():
    # Primary configured to fail
    primary = MockProvider(name="primary_failing", should_fail=True)
    # Secondary fallback provider configured to succeed
    fallback = MockProvider(name="secondary_fallback", mock_content="Fallback success")

    router = GatewayRouter(
        providers={
            "primary_failing": primary,
            "secondary_fallback": fallback,
        },
        routing_order=["primary_failing", "secondary_fallback"],
    )

    messages = [{"role": "user", "content": "Trigger fallback query"}]
    resp, cache_status, provider = await router.route_chat_completion(
        project_id="proj_test",
        model="gpt-4o",
        messages=messages,
    )

    assert cache_status == "MISS"
    assert provider == "secondary_fallback"
    assert resp["choices"][0]["message"]["content"] == "Fallback success"

    # Primary per-project circuit breaker recorded failure (provider-global breaker stays clean)
    assert router.get_circuit_breaker("proj_test", "primary_failing").failure_count == 1
    assert primary.circuit_breaker.failure_count == 0
    # Fallback recorded success
    assert router.get_circuit_breaker("proj_test", "secondary_fallback").failure_count == 0

    # Audit logs should show FALLBACK status
    logs = router.get_logs(limit=10)
    assert len(logs) == 1
    assert logs[0]["status"] == "FALLBACK"
    assert logs[0]["provider"] == "secondary_fallback"


@pytest.mark.asyncio
async def test_router_circuit_breaker_bypasses_open_provider():
    primary = MockProvider(name="primary", failure_threshold=1)
    fallback = MockProvider(name="fallback", mock_content="Bypass success")

    router = GatewayRouter(
        providers={"primary": primary, "fallback": fallback},
        routing_order=["primary", "fallback"],
    )

    # Force trip primary circuit breaker to OPEN for proj_test only
    cb_proj = router.get_circuit_breaker("proj_test", "primary")
    cb_proj.record_failure()
    assert cb_proj.state == CircuitState.OPEN.value

    # Route completion for proj_test: Router should skip primary completely without calling it
    resp, _, provider = await router.route_chat_completion(
        project_id="proj_test",
        model="gpt-4o",
        messages=[{"role": "user", "content": "Skip test"}],
    )

    assert provider == "fallback"
    assert resp["choices"][0]["message"]["content"] == "Bypass success"
    # Primary total_requests remained 0 because breaker was OPEN for proj_test
    assert primary.total_requests == 0


@pytest.mark.asyncio
async def test_router_all_providers_fail_raises_503():
    primary = MockProvider(name="p1", should_fail=True)
    secondary = MockProvider(name="p2", should_fail=True)

    router = GatewayRouter(
        providers={"p1": primary, "p2": secondary},
        routing_order=["p1", "p2"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await router.route_chat_completion(
            project_id="proj_test",
            model="gpt-4o",
            messages=[{"role": "user", "content": "All fail test"}],
        )
    assert exc_info.value.status_code == 503
    assert "All LLM providers unavailable" in exc_info.value.detail


# ============================================================================
# 5. API Endpoints and RBAC Integration Tests
# ============================================================================


def test_gateway_api_auth_rbac(app, auth_setup):
    unauth_client = TestClient(app)
    client = TestClient(app)
    project_id = auth_setup["project_id"]

    headers_viewer = {
        "Authorization": f"Bearer {auth_setup['viewer_token']}",
        "X-Matrix-Project": project_id,
    }
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": project_id,
    }

    payload = {
        "model": "gpt-4o",
        "provider": "mock",
        "messages": [{"role": "user", "content": "Hello Matrix Gateway"}],
    }

    # 1. Unauthenticated -> 401
    resp_unauth = unauth_client.post("/api/v1/gateway/chat/completions", json=payload)
    assert resp_unauth.status_code == 401

    # 2. Viewer role cannot post completions (requires member) -> 403
    resp_viewer_post = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_viewer,
        json=payload,
    )
    assert resp_viewer_post.status_code == 403

    # 3. Member role can post completions with explicit provider="mock" -> 200 (labeled simulated)
    resp_member_post = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_member,
        json=payload,
    )
    assert resp_member_post.status_code == 200
    data = resp_member_post.json()
    assert "choices" in data
    assert "usage" in data
    assert data["x_matrix"]["simulated"] is True
    assert resp_member_post.headers["x-matrix-cache"] == "MISS"
    assert resp_member_post.headers["x-matrix-simulated"] == "true"


def test_gateway_api_chat_completions_caching(app, auth_setup):
    client = TestClient(app)
    project_id = auth_setup["project_id"]
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": project_id,
    }
    payload = {
        "model": "gpt-4o",
        "provider": "mock",
        "messages": [{"role": "user", "content": "Explain quantum computing in one sentence."}],
        "temperature": 0.5,
    }

    # First call -> MISS
    resp1 = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert resp1.status_code == 200
    assert resp1.headers["x-matrix-cache"] == "MISS"
    assert resp1.headers["x-matrix-simulated"] == "true"
    content1 = resp1.json()["choices"][0]["message"]["content"]

    # Second identical call -> HIT (still labeled simulated=true)
    resp2 = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert resp2.status_code == 200
    assert resp2.headers["x-matrix-cache"] == "HIT"
    assert resp2.headers["x-matrix-provider"] == "cache"
    assert resp2.headers["x-matrix-simulated"] == "true"
    assert resp2.json()["x_matrix"]["simulated"] is True
    content2 = resp2.json()["choices"][0]["message"]["content"]
    assert content1 == content2


def test_gateway_api_rate_limiting(app, auth_setup):
    client = TestClient(app)
    project_id = auth_setup["project_id"]
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": project_id,
    }

    # Configure tight rate limit: 1 RPM for this project
    app.state.gateway_router.rate_limiter.set_project_limits(project_id, rpm=1, tpm=10_000)

    payload = {
        "model": "gpt-4o",
        "provider": "mock",
        "messages": [{"role": "user", "content": f"Rate limit check {time.time()}"}],
    }

    # First call succeeds
    r1 = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert r1.status_code == 200

    # Second call within 60s blocked with 429
    payload2 = {
        "model": "gpt-4o",
        "provider": "mock",
        "messages": [{"role": "user", "content": f"Rate limit check second {time.time()}"}],
    }
    r2 = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload2)
    assert r2.status_code == 429
    assert "RPM limit of 1 exceeded" in r2.json()["detail"]


def test_gateway_api_stats_providers_and_logs(app, auth_setup):
    client = TestClient(app)
    project_id = auth_setup["project_id"]
    headers_viewer = {
        "Authorization": f"Bearer {auth_setup['viewer_token']}",
        "X-Matrix-Project": project_id,
    }
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": project_id,
    }

    # Reset router state for clean metrics
    app.state.gateway_router.reset()

    # Trigger a request using explicit mock provider
    payload = {
        "model": "gpt-4o",
        "provider": "mock",
        "messages": [{"role": "user", "content": "Telemetry audit test query"}],
    }
    res = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert res.status_code == 200

    # 1. GET /api/v1/gateway/stats
    stats_res = client.get("/api/v1/gateway/stats", headers=headers_viewer)
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["total_requests"] == 1
    assert "cache_hit_rate" in stats
    assert "tokens_saved" in stats
    assert "cost_saved_usd" in stats
    assert "avg_latency_ms" in stats

    # 2. GET /api/v1/gateway/providers
    prov_res = client.get("/api/v1/gateway/providers", headers=headers_viewer)
    assert prov_res.status_code == 200
    prov_data = prov_res.json()
    assert "providers" in prov_data
    provider_names = [p["name"] for p in prov_data["providers"]]
    assert "mock" in provider_names
    assert "openai" in provider_names
    assert "anthropic" in provider_names

    # 3. GET /api/v1/gateway/logs
    logs_res = client.get("/api/v1/gateway/logs", headers=headers_viewer)
    assert logs_res.status_code == 200
    logs_data = logs_res.json()
    assert logs_data["total"] >= 1
    recent_log = logs_data["logs"][0]
    assert recent_log["model"] == "gpt-4o"
    assert recent_log["status"] in ("DIRECT", "FALLBACK")
    assert recent_log["simulated"] is True
    assert "tokens" in recent_log
    assert "latency_ms" in recent_log


def test_gateway_api_cascading_fallback(app, auth_setup):
    client = TestClient(app)
    project_id = auth_setup["project_id"]
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": project_id,
    }
    headers_viewer = {
        "Authorization": f"Bearer {auth_setup['viewer_token']}",
        "X-Matrix-Project": project_id,
    }

    # Configure custom providers in app router to test API fallback
    primary = MockProvider(name="custom_primary", should_fail=True)
    fallback = MockProvider(name="custom_fallback", mock_content="API fallback succeeded!")

    app.state.gateway_router.register_provider(primary)
    app.state.gateway_router.register_provider(fallback)
    app.state.gateway_router.set_routing_order(["custom_primary", "custom_fallback"])

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Fallback API endpoint query"}],
    }

    # Member requests completion: primary fails, secondary responds
    res = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert res.status_code == 200
    assert res.headers["x-matrix-provider"] == "custom_fallback"
    assert res.json()["choices"][0]["message"]["content"] == "API fallback succeeded!"

    # Viewer checks audit logs: should record FALLBACK
    logs_res = client.get("/api/v1/gateway/logs", headers=headers_viewer)
    assert logs_res.status_code == 200
    logs = logs_res.json()["logs"]
    assert logs[0]["status"] == "FALLBACK"
    assert logs[0]["provider"] == "custom_fallback"


# ============================================================================
# 6. T8 Hardening: No Silent Mock, Streaming 422, Allowlist/Caps, Isolation,
#    Shared DB Rate Limit Across 2 Apps, Error Sanitization & Retention
# ============================================================================


def test_gateway_no_silent_mock_returns_503_provider_not_configured(app, auth_setup, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(app)
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": auth_setup["project_id"],
    }

    resp = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_member,
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Should not silently mock"}],
        },
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "provider_not_configured"
    assert "OPENAI_API_KEY" in detail["missing_env_vars"]
    assert "ANTHROPIC_API_KEY" in detail["missing_env_vars"]

    # Error row recorded in DB logs with sanitized error
    logs_res = client.get("/api/v1/gateway/logs", headers=headers_member)
    assert logs_res.status_code == 200
    assert logs_res.json()["logs"][0]["status"] == "ERROR"
    assert "provider_not_configured" in (logs_res.json()["logs"][0]["error_sanitized"] or "")


def test_gateway_stream_true_returns_422(app, auth_setup):
    client = TestClient(app)
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": auth_setup["project_id"],
    }
    resp = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_member,
        json={
            "model": "gpt-4o",
            "provider": "mock",
            "stream": True,
            "messages": [{"role": "user", "content": "Stream me"}],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "stream_not_supported"


def test_gateway_param_allowlist_and_caps(app, auth_setup):
    client = TestClient(app)
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": auth_setup["project_id"],
    }

    # Unknown params (including base_url/api_key injection attempts) are dropped and reported in x-matrix-warnings
    resp = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_member,
        json={
            "model": "gpt-4o",
            "provider": "mock",
            "messages": [{"role": "user", "content": "Allowlist test"}],
            "base_url": "https://evil.example.com/v1",
            "api_key": "sk-injected-secret-12345",
            "unknown_flag": True,
        },
    )
    assert resp.status_code == 200
    warnings_hdr = resp.headers.get("x-matrix-warnings", "")
    assert "dropped_param:base_url" in warnings_hdr
    assert "dropped_param:api_key" in warnings_hdr
    assert "dropped_param:unknown_flag" in warnings_hdr

    # max_tokens cap -> 422
    resp_cap_tokens = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_member,
        json={
            "model": "gpt-4o",
            "provider": "mock",
            "max_tokens": 999_999,
            "messages": [{"role": "user", "content": "Too many tokens"}],
        },
    )
    assert resp_cap_tokens.status_code == 422
    assert resp_cap_tokens.json()["detail"]["code"] == "max_tokens_cap_exceeded"

    # n cap -> 422
    resp_cap_n = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_member,
        json={
            "model": "gpt-4o",
            "provider": "mock",
            "n": 25,
            "messages": [{"role": "user", "content": "Too many choices"}],
        },
    )
    assert resp_cap_n.status_code == 422
    assert resp_cap_n.json()["detail"]["code"] == "n_cap_exceeded"


def test_gateway_error_sanitization():
    import httpx

    secret = "sk-supersecretkey1234567890"
    long_body = f"upstream failure for Bearer {secret} at https://api.openai.com/v1/chat/completions " + ("x" * 400)
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(500, text=long_body, request=req)
    exc = httpx.HTTPStatusError("err", request=req, response=resp)

    sanitized = sanitize_provider_error(exc, secret_values=[secret], max_body_chars=200)
    assert secret not in sanitized
    assert "https://api.openai.com" not in sanitized
    assert "[redacted-secret]" in sanitized
    assert "[redacted-url]" in sanitized
    # Body portion bounded to <= 200 chars (+ "HTTP 500: " + "...")
    assert len(sanitized) <= 220


def test_gateway_project_isolation_all_endpoints(app, auth_setup):
    client = TestClient(app)
    proj_a = auth_setup["project_id"]
    proj_b = auth_setup["other_project_id"]
    headers_a = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": proj_a,
    }
    headers_b = {
        "Authorization": f"Bearer {auth_setup['other_member_token']}",
        "X-Matrix-Project": proj_b,
    }

    app.state.gateway_router.rate_limiter.set_project_limits(proj_a, rpm=12, tpm=50_000)
    app.state.gateway_router.rate_limiter.set_project_limits(proj_b, rpm=34, tpm=50_000)

    shared_payload = {
        "model": "gpt-4o",
        "provider": "mock",
        "messages": [{"role": "user", "content": "Confidential prompt for tenant A"}],
        "temperature": 0.0,
    }

    # Project A calls twice -> MISS then HIT
    r_a1 = client.post("/api/v1/gateway/chat/completions", headers=headers_a, json=shared_payload)
    assert r_a1.status_code == 200
    assert r_a1.headers["x-matrix-cache"] == "MISS"

    r_a2 = client.post("/api/v1/gateway/chat/completions", headers=headers_a, json=shared_payload)
    assert r_a2.status_code == 200
    assert r_a2.headers["x-matrix-cache"] == "HIT"

    # Project B calls identical payload -> MUST BE MISS (cache key includes project_id)
    r_b1 = client.post("/api/v1/gateway/chat/completions", headers=headers_b, json=shared_payload)
    assert r_b1.status_code == 200
    assert r_b1.headers["x-matrix-cache"] == "MISS"

    # Stats isolated per project
    stats_a = client.get("/api/v1/gateway/stats", headers=headers_a).json()
    stats_b = client.get("/api/v1/gateway/stats", headers=headers_b).json()
    assert stats_a["total_requests"] == 2
    assert stats_a["cache_hits"] == 1
    assert stats_b["total_requests"] == 1
    assert stats_b["cache_hits"] == 0

    # Logs isolated per project
    logs_a = client.get("/api/v1/gateway/logs", headers=headers_a).json()["logs"]
    logs_b = client.get("/api/v1/gateway/logs", headers=headers_b).json()["logs"]
    assert all(row["project_id"] == proj_a for row in logs_a)
    assert all(row["project_id"] == proj_b for row in logs_b)
    assert len(logs_a) == 2
    assert len(logs_b) == 1

    # Providers metrics isolated per project
    prov_a = {p["name"]: p for p in client.get("/api/v1/gateway/providers", headers=headers_a).json()["providers"]}
    prov_b = {p["name"]: p for p in client.get("/api/v1/gateway/providers", headers=headers_b).json()["providers"]}
    assert prov_a["mock"]["total_requests"] == 1
    assert prov_b["mock"]["total_requests"] == 1

    # Rules reflect per-project rate limit and do not include mock in production fallback
    rules_a = client.get("/api/v1/gateway/rules", headers=headers_a).json()["rules"]
    rules_b = client.get("/api/v1/gateway/rules", headers=headers_b).json()["rules"]
    assert rules_a[0]["rate_limit_rpm"] == 12
    assert rules_b[0]["rate_limit_rpm"] == 34
    assert all("mock" not in fb for r in rules_a for fb in r["fallback_chain"])


def test_gateway_shared_rate_limit_across_two_app_instances(tmp_path):
    """Proves 2 separate FastAPI app instances sharing the same DB enforce a shared rate limit."""
    db_url = f"sqlite:///{tmp_path}/shared_gw.db"
    app1 = create_app(Settings(database_url=db_url, span_store="memory"))
    app2 = create_app(Settings(database_url=db_url, span_store="memory"))

    factory = app1.state.session_factory
    with factory() as db:
        project, _ = create_project(db, "Shared RL Project", with_key=False)
        db.commit()
        project_id = project.id
    user_id = make_user(factory)
    add_membership(factory, user_id, project_id, Role.MEMBER)
    token = session_token(factory, user_id)
    headers = {"Authorization": f"Bearer {token}", "X-Matrix-Project": project_id}

    # Both workers have the same RPM=2 limit for this project
    app1.state.gateway_router.rate_limiter.set_project_limits(project_id, rpm=2, tpm=50_000)
    app2.state.gateway_router.rate_limiter.set_project_limits(project_id, rpm=2, tpm=50_000)

    client1 = TestClient(app1)
    client2 = TestClient(app2)

    # Request 1 on worker 1 -> 200
    r1 = client1.post(
        "/api/v1/gateway/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "provider": "mock", "messages": [{"role": "user", "content": "w1 req1"}]},
    )
    assert r1.status_code == 200

    # Request 2 on worker 2 -> 200
    r2 = client2.post(
        "/api/v1/gateway/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "provider": "mock", "messages": [{"role": "user", "content": "w2 req2"}]},
    )
    assert r2.status_code == 200

    # Request 3 on worker 1 -> 429 (shared counter is already at 2!)
    r3 = client1.post(
        "/api/v1/gateway/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "provider": "mock", "messages": [{"role": "user", "content": "w1 req3"}]},
    )
    assert r3.status_code == 429

    # Request 4 on worker 2 -> 429 as well
    r4 = client2.post(
        "/api/v1/gateway/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "provider": "mock", "messages": [{"role": "user", "content": "w2 req4"}]},
    )
    assert r4.status_code == 429

    # Shared DB stats: worker 2 sees both requests logged across worker 1 & worker 2
    stats2 = client2.get("/api/v1/gateway/stats", headers=headers).json()
    assert stats2["total_requests"] == 2


def test_gateway_retention_cleanup(app, auth_setup):
    from datetime import UTC, datetime, timedelta

    factory = app.state.session_factory
    router = app.state.gateway_router
    router.bind_session_factory(factory)

    old_ts = datetime.now(UTC) - timedelta(days=45)
    recent_ts = datetime.now(UTC)
    with factory() as db:
        db.add(
            GatewayRequest(
                id="gw-old",
                project_id=auth_setup["project_id"],
                ts=old_ts,
                route="/api/v1/gateway/chat/completions",
                model="gpt-4o",
                provider="mock",
                status="DIRECT",
                latency_ms=1.0,
                input_tokens=5,
                output_tokens=5,
                cost_usd=0.0,
                cache_hit=False,
                simulated=True,
            )
        )
        db.add(
            GatewayRequest(
                id="gw-new",
                project_id=auth_setup["project_id"],
                ts=recent_ts,
                route="/api/v1/gateway/chat/completions",
                model="gpt-4o",
                provider="mock",
                status="DIRECT",
                latency_ms=1.0,
                input_tokens=5,
                output_tokens=5,
                cost_usd=0.0,
                cache_hit=False,
                simulated=True,
            )
        )
        db.add(
            GatewayRateWindow(
                project_id=auth_setup["project_id"],
                scope="default",
                window_start=1000,
                request_count=5,
                token_count=100,
            )
        )
        db.commit()

    deleted_reqs = router.cleanup_expired_requests()
    deleted_wins = router.rate_limiter.cleanup_expired_windows()
    assert deleted_reqs == 1
    assert deleted_wins == 1

    logs = router.get_logs(limit=10, project_id=auth_setup["project_id"])
    assert [row["id"] for row in logs] == ["gw-new"]


# ============================================================================
# 7. DB Rate Limiter (SQLite & PostgreSQL), SQL Telemetry Aggregations,
#    Threadpool Offload, Provider HTTP Translation & Cross-Tenant CB Isolation
# ============================================================================


def test_rate_limiter_db_acquire_rpm_and_tpm(db_factory):
    limiter = GatewayRateLimiter(default_rpm=5, default_tpm=100, session_factory=db_factory)
    project_id = "proj-db-rl"
    limiter.set_project_limits(project_id, rpm=3, tpm=50)

    # 1. First acquire succeeds (1 req, 30 tokens)
    ok1, reason1, ws1 = limiter.acquire(project_id, estimated_tokens=30)
    assert ok1 is True
    assert reason1 is None

    # 2. Exceeding TPM limit rolls back cleanly without incrementing RPM/TPM
    ok_tpm, reason_tpm, _ = limiter.acquire(project_id, estimated_tokens=25)
    assert ok_tpm is False
    assert "TPM limit of 50 exceeded" in (reason_tpm or "")

    with db_factory() as db:
        row = db.scalar(
            select(GatewayRateWindow).where(
                GatewayRateWindow.project_id == project_id,
                GatewayRateWindow.window_start == ws1,
            )
        )
        assert row is not None
        assert row.request_count == 1
        assert row.token_count == 30

    # 3. Two more acquires within TPM & RPM succeed (3 req, 50 tokens)
    assert limiter.acquire(project_id, estimated_tokens=10)[0] is True
    assert limiter.acquire(project_id, estimated_tokens=10)[0] is True

    # 4. Fourth acquire blocked by RPM limit and rolled back
    ok_rpm, reason_rpm, _ = limiter.acquire(project_id, estimated_tokens=0)
    assert ok_rpm is False
    assert "RPM limit of 3 exceeded" in (reason_rpm or "")

    usage = limiter.get_usage(project_id)
    assert usage["rpm_current"] == 3
    assert usage["tpm_current"] == 50


def test_rate_limiter_db_reconcile_tokens_and_cache_refund(db_factory):
    limiter = GatewayRateLimiter(default_rpm=10, default_tpm=100, session_factory=db_factory)
    project_id = "proj-db-reconcile"

    ok1, _, ws = limiter.acquire(project_id, estimated_tokens=40)
    assert ok1 is True
    limiter.reconcile_tokens(
        project_id=project_id,
        window_start=ws,
        estimated_tokens=40,
        actual_tokens=15,
    )
    assert limiter.get_usage(project_id)["tpm_current"] == 15

    # Cache hit reserves 30 tokens then refunds all 30 (actual_tokens=0)
    ok2, _, ws2 = limiter.acquire(project_id, estimated_tokens=30)
    assert ok2 is True
    assert ws2 == ws
    limiter.reconcile_tokens(
        project_id=project_id,
        window_start=ws2,
        estimated_tokens=30,
        actual_tokens=0,
    )
    usage = limiter.get_usage(project_id)
    assert usage["rpm_current"] == 2
    assert usage["tpm_current"] == 15

    # Clamps safely at 0 if refund exceeds current balance
    limiter.reconcile_tokens(
        project_id=project_id,
        window_start=ws2,
        estimated_tokens=100,
        actual_tokens=0,
    )
    assert limiter.get_usage(project_id)["tpm_current"] == 0


def test_rate_limiter_db_batched_cleanup_expired_windows(db_factory):
    limiter = GatewayRateLimiter(
        session_factory=db_factory,
        window_retention_seconds=3600,
        cleanup_batch_size=3,
    )
    with db_factory() as db:
        for i in range(5):
            db.add(
                GatewayRateWindow(
                    project_id=f"proj-old-{i}",
                    scope="default",
                    window_start=100 + i * 60,
                    request_count=1,
                    token_count=10,
                )
            )
        for j in range(2):
            db.add(
                GatewayRateWindow(
                    project_id=f"proj-fresh-{j}",
                    scope="default",
                    window_start=9_500 + j * 60,
                    request_count=2,
                    token_count=20,
                )
            )
        db.commit()

    delete_stmts: list[str] = []
    eng = db_factory.kw["bind"]

    def _capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("DELETE"):
            delete_stmts.append(statement)

    event.listen(eng, "before_cursor_execute", _capture_sql)
    try:
        deleted_1 = limiter.cleanup_expired_windows(current_window_start=10_000)
        deleted_2 = limiter.cleanup_expired_windows(current_window_start=10_000)
        deleted_3 = limiter.cleanup_expired_windows(current_window_start=10_000)
    finally:
        event.remove(eng, "before_cursor_execute", _capture_sql)

    assert deleted_1 == 3
    assert deleted_2 == 2
    assert deleted_3 == 0
    # Exactly one batched SQL DELETE statement per non-empty cleanup pass (plus 1 no-op pass)
    assert len(delete_stmts) == 3

    with db_factory() as db:
        remaining = db.scalars(select(GatewayRateWindow.project_id)).all()
    assert sorted(remaining) == ["proj-fresh-0", "proj-fresh-1"]


def test_gateway_stats_and_providers_sql_aggregation(db_factory):
    router = GatewayRouter(session_factory=db_factory, retention_days=30)
    now = datetime.now(UTC)

    with db_factory() as db:
        db.add_all(
            [
                GatewayRequest(
                    id="r-hit",
                    project_id="proj-a",
                    ts=now - timedelta(minutes=5),
                    model="gpt-4o",
                    provider="cache",
                    status="CACHE_HIT",
                    latency_ms=10.0,
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.02,
                    cache_hit=True,
                    simulated=False,
                ),
                GatewayRequest(
                    id="r-dir",
                    project_id="proj-a",
                    ts=now - timedelta(hours=1),
                    model="gpt-4o",
                    provider="openai",
                    status="DIRECT",
                    latency_ms=30.0,
                    input_tokens=20,
                    output_tokens=10,
                    cost_usd=0.05,
                    cache_hit=False,
                    simulated=False,
                ),
                GatewayRequest(
                    id="r-fb-recent",
                    project_id="proj-a",
                    ts=now - timedelta(hours=2),
                    model="gpt-4o",
                    provider="anthropic",
                    status="FALLBACK",
                    latency_ms=50.0,
                    input_tokens=20,
                    output_tokens=10,
                    cost_usd=0.03,
                    cache_hit=False,
                    simulated=False,
                ),
                GatewayRequest(
                    id="r-fb-old-30d",
                    project_id="proj-a",
                    ts=now - timedelta(hours=48),
                    model="gpt-4o",
                    provider="anthropic",
                    status="FALLBACK",
                    latency_ms=70.0,
                    input_tokens=20,
                    output_tokens=10,
                    cost_usd=0.04,
                    cache_hit=False,
                    simulated=False,
                ),
                GatewayRequest(
                    id="r-err",
                    project_id="proj-a",
                    ts=now - timedelta(minutes=1),
                    model="gpt-4o",
                    provider="openai",
                    status="ERROR",
                    latency_ms=20.0,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.99,
                    cache_hit=False,
                    simulated=False,
                ),
                GatewayRequest(
                    id="r-expired",
                    project_id="proj-a",
                    ts=now - timedelta(days=45),
                    model="gpt-4o",
                    provider="openai",
                    status="FALLBACK",
                    latency_ms=999.0,
                    input_tokens=100,
                    output_tokens=100,
                    cost_usd=5.0,
                    cache_hit=False,
                    simulated=False,
                ),
                GatewayRequest(
                    id="r-other-proj",
                    project_id="proj-b",
                    ts=now - timedelta(minutes=2),
                    model="gpt-4o",
                    provider="openai",
                    status="FALLBACK",
                    latency_ms=500.0,
                    input_tokens=500,
                    output_tokens=500,
                    cost_usd=9.0,
                    cache_hit=False,
                    simulated=False,
                ),
            ]
        )
        db.commit()

    executed_sql: list[str] = []
    eng = db_factory.kw["bind"]

    def _record_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        executed_sql.append(statement)

    event.listen(eng, "before_cursor_execute", _record_sql)
    try:
        stats = router.get_stats(project_id="proj-a")
        providers = {p["name"]: p for p in router.get_providers_status(project_id="proj-a")}
    finally:
        event.remove(eng, "before_cursor_execute", _record_sql)

    assert stats["total_requests"] == 5
    assert stats["cache_hits"] == 1
    assert stats["cache_hit_rate"] == 0.2
    assert stats["tokens_saved"] == 150
    assert stats["cost_saved_usd"] == 0.02
    assert stats["total_cost_usd"] == 0.12
    assert stats["avg_latency_ms"] == 36.0
    assert stats["latency_p50_ms"] == 30.0
    assert stats["latency_p99_ms"] == pytest.approx(69.2, abs=0.01)
    assert stats["fallback_events_24h"] == 1

    assert providers["openai"]["total_requests"] == 2
    assert providers["openai"]["avg_latency_ms"] == 25.0
    assert providers["anthropic"]["total_requests"] == 2
    assert providers["anthropic"]["avg_latency_ms"] == 60.0
    assert providers["mock"]["total_requests"] == 0

    assert any("GROUP BY" in sql.upper() for sql in executed_sql)
    assert not any("SELECT gateway_requests.id, gateway_requests.project_id" in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_route_chat_completion_offloads_db_io_to_threadpool(app, auth_setup, monkeypatch):
    router = app.state.gateway_router
    router.bind_session_factory(app.state.session_factory)

    loop_thread_id = threading.get_ident()
    seen_threads: dict[str, int] = {}

    orig_acquire = router.rate_limiter.acquire
    orig_reconcile = router.rate_limiter.reconcile_tokens
    orig_record_log = router._record_log

    def wrapped_acquire(*args, **kwargs):
        seen_threads["acquire"] = threading.get_ident()
        return orig_acquire(*args, **kwargs)

    def wrapped_reconcile(*args, **kwargs):
        seen_threads["reconcile_tokens"] = threading.get_ident()
        return orig_reconcile(*args, **kwargs)

    def wrapped_record_log(*args, **kwargs):
        seen_threads["_record_log"] = threading.get_ident()
        return orig_record_log(*args, **kwargs)

    monkeypatch.setattr(router.rate_limiter, "acquire", wrapped_acquire)
    monkeypatch.setattr(router.rate_limiter, "reconcile_tokens", wrapped_reconcile)
    monkeypatch.setattr(router, "_record_log", wrapped_record_log)

    resp, cache_status, provider = await router.route_chat_completion(
        project_id=auth_setup["project_id"],
        model="gpt-4o",
        provider="mock",
        messages=[{"role": "user", "content": "Threadpool offload test"}],
    )
    assert cache_status == "MISS"
    assert provider == "mock"
    assert resp["x_matrix"]["simulated"] is True
    assert set(seen_threads.keys()) == {"acquire", "reconcile_tokens", "_record_log"}
    assert all(tid != loop_thread_id for tid in seen_threads.values())


@pytest.mark.asyncio
async def test_openai_provider_complete_success_and_error_classification():
    captured: list[tuple[httpx.Request, dict]] = []
    secret = "sk-openai-secret-987654321"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured.append((request, body))
        mode = body["messages"][0]["content"]
        if mode == "ok":
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-oa-1",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        if mode.startswith("http_"):
            code = int(mode.split("_")[1])
            return httpx.Response(code, text=f"Upstream {code} with key {secret}")
        if mode == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("connection refused", request=request)

    prov = OpenAIProvider(api_key=secret, transport=httpx.MockTransport(handler))

    res = await prov.complete(
        messages=[{"role": "user", "content": "ok"}],
        model="gpt-4o",
        temperature=0.2,
        max_tokens=128,
        seed=7,
        base_url="https://evil.invalid",
    )
    assert res["choices"][0]["message"]["content"] == "Hello!"
    req, sent_body = captured[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {secret}"
    assert sent_body["seed"] == 7
    assert "base_url" not in sent_body

    # Client-induced 4xx must NOT trip the circuit breaker
    for status_code in (400, 401, 403, 404, 422):
        with pytest.raises(ProviderExecutionError) as exc_info:
            await prov.complete(messages=[{"role": "user", "content": f"http_{status_code}"}], model="gpt-4o")
        assert exc_info.value.status_code == status_code
        assert exc_info.value.trips_circuit_breaker is False
        assert secret not in str(exc_info.value)

    # 429, 5xx, timeout, and connection error MUST trip the circuit breaker
    for status_code in (429, 500, 503):
        with pytest.raises(ProviderExecutionError) as exc_info:
            await prov.complete(messages=[{"role": "user", "content": f"http_{status_code}"}], model="gpt-4o")
        assert exc_info.value.status_code == status_code
        assert exc_info.value.trips_circuit_breaker is True

    for mode in ("timeout", "connect"):
        with pytest.raises(ProviderExecutionError) as exc_info:
            await prov.complete(messages=[{"role": "user", "content": mode}], model="gpt-4o")
        assert exc_info.value.status_code is None
        assert exc_info.value.trips_circuit_breaker is True


@pytest.mark.asyncio
async def test_anthropic_provider_complete_translation_and_errors():
    captured: list[tuple[httpx.Request, dict]] = []
    secret = "sk-ant-secret-1234567890"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured.append((request, body))
        user_msg = body["messages"][0]["content"]
        if user_msg == "ok":
            return httpx.Response(
                200,
                json={
                    "id": "msg_123",
                    "content": [{"type": "text", "text": "Claude answer"}],
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                },
            )
        if user_msg.startswith("http_"):
            code = int(user_msg.split("_")[1])
            return httpx.Response(code, text=f"Anthropic {code} {secret}")
        raise httpx.ReadTimeout("timeout", request=request)

    prov = AnthropicProvider(api_key=secret, transport=httpx.MockTransport(handler))

    res = await prov.complete(
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "ok"},
        ],
        model="claude-3-5-sonnet-20241022",
        temperature=0.3,
        max_tokens=256,
        stop="END",
        unknown_arg="ignored",
    )
    assert res["object"] == "chat.completion"
    assert res["choices"][0]["message"]["content"] == "Claude answer"
    assert res["usage"] == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}

    req, sent_body = captured[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == secret
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert sent_body["system"] == "Be concise."
    assert sent_body["messages"] == [{"role": "user", "content": "ok"}]
    assert sent_body["stop_sequences"] == ["END"]
    assert "unknown_arg" not in sent_body

    # 4xx does not trip circuit breaker; 429/5xx/timeout do
    for status_code in (400, 401, 403, 404, 422):
        with pytest.raises(ProviderExecutionError) as exc_info:
            await prov.complete(messages=[{"role": "user", "content": f"http_{status_code}"}], model="claude-3-5-sonnet-20241022")
        assert exc_info.value.trips_circuit_breaker is False
        assert secret not in str(exc_info.value)

    for mode in ("http_429", "http_502", "timeout"):
        with pytest.raises(ProviderExecutionError) as exc_info:
            await prov.complete(messages=[{"role": "user", "content": mode}], model="claude-3-5-sonnet-20241022")
        assert exc_info.value.trips_circuit_breaker is True


def test_cross_tenant_circuit_breaker_isolation_and_4xx_non_tripping(app, auth_setup):
    client = TestClient(app)
    proj_a = auth_setup["project_id"]
    proj_b = auth_setup["other_project_id"]
    headers_a = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "X-Matrix-Project": proj_a,
    }
    headers_b = {
        "Authorization": f"Bearer {auth_setup['other_member_token']}",
        "X-Matrix-Project": proj_b,
    }

    openai_calls: list[str] = []

    def openai_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = body["messages"][0]["content"]
        openai_calls.append(prompt)
        if prompt.startswith("client_err_"):
            code = int(prompt.split("_")[-1])
            return httpx.Response(code, json={"error": {"message": f"bad request {code}"}})
        if prompt.startswith("server_err_"):
            return httpx.Response(503, json={"error": {"message": "upstream overloaded"}})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-oa-ok",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": f"oa:{prompt}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )

    def anthropic_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg-fallback",
                "content": [{"type": "text", "text": "anthropic fallback ok"}],
                "usage": {"input_tokens": 5, "output_tokens": 4},
            },
        )

    gw = app.state.gateway_router
    gw.register_provider(
        OpenAIProvider(api_key="sk-oa-test", failure_threshold=3, transport=httpx.MockTransport(openai_handler))
    )
    gw.register_provider(
        AnthropicProvider(api_key="sk-ant-test", failure_threshold=3, transport=httpx.MockTransport(anthropic_handler))
    )
    gw.set_routing_order(["openai", "anthropic"])

    # 1. Five client-induced 4xx errors on Project A must NOT trip openai breaker
    for code in (400, 401, 403, 404, 422):
        r = client.post(
            "/api/v1/gateway/chat/completions",
            headers=headers_a,
            json={
                "model": "gpt-4o",
                "provider": "openai",
                "messages": [{"role": "user", "content": f"client_err_{code}"}],
            },
        )
        assert r.status_code == 503

    prov_a_after_4xx = {p["name"]: p for p in client.get("/api/v1/gateway/providers", headers=headers_a).json()["providers"]}
    stats_a_after_4xx = client.get("/api/v1/gateway/stats", headers=headers_a).json()
    assert prov_a_after_4xx["openai"]["circuit_breaker"] == "CLOSED"
    assert prov_a_after_4xx["openai"]["failure_count"] == 0
    assert stats_a_after_4xx["healthy_providers"] == 3

    # 2. Three 503 errors on Project A trip ONLY Project A's openai circuit breaker
    for idx in range(3):
        r = client.post(
            "/api/v1/gateway/chat/completions",
            headers=headers_a,
            json={
                "model": "gpt-4o",
                "provider": "openai",
                "messages": [{"role": "user", "content": f"server_err_{idx}"}],
            },
        )
        assert r.status_code == 503

    prov_a = {p["name"]: p for p in client.get("/api/v1/gateway/providers", headers=headers_a).json()["providers"]}
    stats_a = client.get("/api/v1/gateway/stats", headers=headers_a).json()
    assert prov_a["openai"]["circuit_breaker"] == "OPEN"
    assert prov_a["openai"]["failure_count"] == 3
    assert stats_a["healthy_providers"] == 2

    # Next request for Project A bypasses OPEN openai and falls back to anthropic
    calls_before_bypass = len(openai_calls)
    r_fb = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_a,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "trigger cascade"}]},
    )
    assert r_fb.status_code == 200
    assert r_fb.headers["x-matrix-provider"] == "anthropic"
    assert len(openai_calls) == calls_before_bypass

    # 3. Project B MUST NOT see Project A's breaker state or be blocked from openai
    prov_b = {p["name"]: p for p in client.get("/api/v1/gateway/providers", headers=headers_b).json()["providers"]}
    stats_b = client.get("/api/v1/gateway/stats", headers=headers_b).json()
    assert prov_b["openai"]["circuit_breaker"] == "CLOSED"
    assert prov_b["openai"]["failure_count"] == 0
    assert stats_b["healthy_providers"] == 3

    r_b = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_b,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "tenant b ok"}]},
    )
    assert r_b.status_code == 200
    assert r_b.headers["x-matrix-provider"] == "openai"
    assert r_b.json()["choices"][0]["message"]["content"] == "oa:tenant b ok"


def test_gateway_circuit_breaker_lru_eviction():
    router = GatewayRouter(max_circuit_breakers=3)
    cb1 = router.get_circuit_breaker("p1", "openai")
    router.get_circuit_breaker("p2", "openai")
    router.get_circuit_breaker("p3", "openai")
    # Touch p1 again so p2 becomes least-recently-used
    assert router.get_circuit_breaker("p1", "openai") is cb1
    router.get_circuit_breaker("p4", "openai")

    assert len(router._circuit_breakers) == 3
    assert ("p1", "openai") in router._circuit_breakers
    assert ("p2", "openai") not in router._circuit_breakers
    assert ("p3", "openai") in router._circuit_breakers
    assert ("p4", "openai") in router._circuit_breakers
