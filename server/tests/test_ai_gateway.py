"""Comprehensive Test Suite for MATRIX AI Gateway (Layer 2 Blueprint SOTA).

Covers:
- Circuit Breaker state transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
- Gateway Cache (SHA-256 hash, exact/semantic lookup, TTL, tokens & USD cost tracking)
- Rate Limiter (RPM & TPM per project with sliding window)
- Gateway Router (direct routing, caching, circuit breaker tripping, cascading fallback)
- API Endpoints & RBAC (chat completions, stats, providers, logs, headers)
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.gateway.cache import GatewayCache, compute_cache_key
from matrix_server.gateway.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from matrix_server.gateway.rate_limiter import GatewayRateLimiter
from matrix_server.gateway.router import GatewayRouter, MockProvider


@pytest.fixture
def app(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/gateway_test.db", span_store="memory")
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_setup(client):
    """Sets up an admin, a member, and a viewer in the same project."""
    # 1. Register admin
    admin_res = client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@matrix.local",
            "password": "Password123!",
            "name": "Admin Operator",
            "project_name": "Gateway Test Project",
        },
    ).json()
    admin_token = admin_res["token"]
    project_id = admin_res["project"]["id"]

    # 2. Register and add member
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "member@matrix.local",
            "password": "Password123!",
            "name": "Member Engineer",
            "project_name": "Member Alt Project",
        },
    )
    client.post(
        f"/api/v1/projects/{project_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": "member@matrix.local", "role": "member"},
    )
    member_login = client.post(
        "/api/v1/auth/login",
        json={"email": "member@matrix.local", "password": "Password123!"},
    ).json()
    member_token = member_login["token"]

    # 3. Register and add viewer
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "viewer@matrix.local",
            "password": "Password123!",
            "name": "Viewer Auditor",
            "project_name": "Viewer Alt Project",
        },
    )
    client.post(
        f"/api/v1/projects/{project_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": "viewer@matrix.local", "role": "viewer"},
    )
    viewer_login = client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@matrix.local", "password": "Password123!"},
    ).json()
    viewer_token = viewer_login["token"]

    return {
        "project_id": project_id,
        "admin_token": admin_token,
        "member_token": member_token,
        "viewer_token": viewer_token,
    }


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

    # Primary circuit breaker recorded failure
    assert primary.circuit_breaker.failure_count == 1
    # Fallback recorded success
    assert fallback.circuit_breaker.failure_count == 0

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

    # Force trip primary circuit breaker to OPEN
    primary.circuit_breaker.record_failure()
    assert primary.circuit_breaker.state == CircuitState.OPEN.value

    # Route completion: Router should skip primary completely without calling it
    resp, _, provider = await router.route_chat_completion(
        project_id="proj_test",
        model="gpt-4o",
        messages=[{"role": "user", "content": "Skip test"}],
    )

    assert provider == "fallback"
    assert resp["choices"][0]["message"]["content"] == "Bypass success"
    # Primary total_requests remained 0 because breaker was OPEN
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
        "x-matrix-project-id": project_id,
    }
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "x-matrix-project-id": project_id,
    }

    payload = {
        "model": "gpt-4o",
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

    # 3. Member role can post completions -> 200
    resp_member_post = client.post(
        "/api/v1/gateway/chat/completions",
        headers=headers_member,
        json=payload,
    )
    assert resp_member_post.status_code == 200
    data = resp_member_post.json()
    assert "choices" in data
    assert "usage" in data
    assert resp_member_post.headers["x-matrix-cache"] == "MISS"


def test_gateway_api_chat_completions_caching(app, auth_setup):
    client = TestClient(app)
    project_id = auth_setup["project_id"]
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "x-matrix-project-id": project_id,
    }
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Explain quantum computing in one sentence."}],
        "temperature": 0.5,
    }

    # First call -> MISS
    resp1 = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert resp1.status_code == 200
    assert resp1.headers["x-matrix-cache"] == "MISS"
    content1 = resp1.json()["choices"][0]["message"]["content"]

    # Second identical call -> HIT
    resp2 = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert resp2.status_code == 200
    assert resp2.headers["x-matrix-cache"] == "HIT"
    assert resp2.headers["x-matrix-provider"] == "cache"
    content2 = resp2.json()["choices"][0]["message"]["content"]
    assert content1 == content2


def test_gateway_api_rate_limiting(app, auth_setup):
    client = TestClient(app)
    project_id = auth_setup["project_id"]
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "x-matrix-project-id": project_id,
    }

    # Configure tight rate limit: 1 RPM for this project
    app.state.gateway_router.rate_limiter.set_project_limits(project_id, rpm=1, tpm=10_000)

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": f"Rate limit check {time.time()}"}],
    }

    # First call succeeds
    r1 = client.post("/api/v1/gateway/chat/completions", headers=headers_member, json=payload)
    assert r1.status_code == 200

    # Second call within 60s blocked with 429
    payload2 = {
        "model": "gpt-4o",
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
        "x-matrix-project-id": project_id,
    }
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "x-matrix-project-id": project_id,
    }

    # Reset router state for clean metrics
    app.state.gateway_router.reset()

    # Trigger a request
    payload = {
        "model": "gpt-4o",
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
    assert "tokens" in recent_log
    assert "latency_ms" in recent_log


def test_gateway_api_cascading_fallback(app, auth_setup):
    client = TestClient(app)
    project_id = auth_setup["project_id"]
    headers_member = {
        "Authorization": f"Bearer {auth_setup['member_token']}",
        "x-matrix-project-id": project_id,
    }
    headers_viewer = {
        "Authorization": f"Bearer {auth_setup['viewer_token']}",
        "x-matrix-project-id": project_id,
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
