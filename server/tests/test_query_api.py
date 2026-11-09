import math
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from otlp_payloads import AGENT_ID, AGENT_TRACE_ID, ERROR_TRACE_ID, GENAI_TRACE_ID, full_request

from matrix_server.api import query
from matrix_server.app import create_app
from matrix_server.config import Settings

ADMIN = {"Authorization": "Bearer adm"}
ALL_IDS = {AGENT_TRACE_ID, GENAI_TRACE_ID, ERROR_TRACE_ID}


def _project(client, name):
    resp = client.post("/api/v1/admin/projects", json={"name": name}, headers=ADMIN)
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['api_key']}"}


@pytest.fixture
def env(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/m.db", admin_token="adm"))
    client = TestClient(app)
    auth = _project(client, "one")
    resp = client.post(
        "/v1/traces",
        content=full_request().SerializeToString(),
        headers={"Content-Type": "application/x-protobuf", **auth},
    )
    assert resp.status_code == 200
    return client, auth


def test_requires_auth(env):
    client, _ = env
    assert client.get("/api/v1/traces").status_code == 401
    assert client.post("/api/v1/scores", json={}).status_code == 401


def test_list_filters_and_shape(env):
    client, auth = env
    items = client.get("/api/v1/traces", headers=auth).json()["items"]
    assert {t["trace_id"] for t in items} == ALL_IDS
    assert "project_id" not in items[0]
    assert set(items[0]) >= {"trace_id", "name", "start_time", "duration_ms", "root_kind", "scores", "tags"}

    def ids(**params):
        return {
            t["trace_id"] for t in client.get("/api/v1/traces", params=params, headers=auth).json()["items"]
        }

    assert ids(status="error") == {ERROR_TRACE_ID}
    assert ids(session_id="sess-2") == {ERROR_TRACE_ID}
    assert ids(user_id="user-2") == {ERROR_TRACE_ID}
    assert ids(name="pipeline") == {ERROR_TRACE_ID}
    assert ids(kind="AGENT") == {AGENT_TRACE_ID}
    assert ids(since="2999-01-01T00:00:00Z") == set()
    assert ids(until="2000-01-01T00:00:00Z") == set()
    assert ids(since="2000-01-01T00:00:00Z", until="2999-01-01T00:00:00Z") == ALL_IDS


def test_pagination_bad_cursor_and_limit(env):
    client, auth = env
    seen, cursor = [], None
    for _ in range(5):
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        page = client.get("/api/v1/traces", params=params, headers=auth).json()
        seen += [t["trace_id"] for t in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(ALL_IDS)
    assert client.get("/api/v1/traces", params={"cursor": "!!bad"}, headers=auth).status_code == 400
    assert client.get("/api/v1/traces", params={"limit": 501}, headers=auth).status_code == 422
    assert client.get("/api/v1/traces", params={"limit": 0}, headers=auth).status_code == 422
    assert client.get("/api/v1/traces", params={"limit": 500}, headers=auth).status_code == 200
    assert client.get("/api/v1/sessions", params={"cursor": "!!bad"}, headers=auth).status_code == 400


def test_trace_detail(env):
    client, auth = env
    body = client.get(f"/api/v1/traces/{AGENT_TRACE_ID}", headers=auth).json()
    assert body["trace"]["trace_id"] == AGENT_TRACE_ID
    assert body["trace"]["span_count"] == len(body["spans"]) > 1
    assert any(s["span_id"] == AGENT_ID for s in body["spans"])
    assert all("project_id" not in s for s in body["spans"])
    assert client.get("/api/v1/traces/" + "0" * 32, headers=auth).status_code == 404


def test_sessions(env):
    client, auth = env
    page = client.get("/api/v1/sessions", headers=auth).json()
    assert "sess-2" in {s["session_id"] for s in page["items"]}
    detail = client.get("/api/v1/sessions/sess-2", headers=auth).json()
    assert detail["session"]["session_id"] == "sess-2"
    assert [t["trace_id"] for t in detail["traces"]] == [ERROR_TRACE_ID]
    assert client.get("/api/v1/sessions/nope", headers=auth).status_code == 404


def test_overview(env):
    client, auth = env
    data = client.get("/api/v1/stats/overview", headers=auth).json()
    assert data["trace_count"] == 3
    assert data["error_rate"] == pytest.approx(1 / 3)
    assert set(data) >= {
        "p50_latency_ms",
        "p95_latency_ms",
        "total_cost_usd",
        "input_tokens",
        "output_tokens",
        "by_model",
    }
    # fixtures are from 2026-11-09; shorthand windows relative to now exclude them only if old enough
    for since in ("1h", "24h", "7d"):
        assert client.get("/api/v1/stats/overview", params={"since": since}, headers=auth).status_code == 200
    assert (
        client.get("/api/v1/stats/overview", params={"since": "2999-01-01T00:00:00Z"}, headers=auth).json()[
            "trace_count"
        ]
        == 0
    )
    assert (
        client.get("/api/v1/stats/overview", params={"since": "2000-01-01"}, headers=auth).json()[
            "trace_count"
        ]
        == 3
    )
    assert client.get("/api/v1/stats/overview", params={"since": "garbage"}, headers=auth).status_code == 422


def test_parse_since_shorthand():
    now = datetime.now(UTC)
    assert abs((now - query.parse_since("24h")) - timedelta(hours=24)) < timedelta(seconds=5)
    assert abs((now - query.parse_since("7d")) - timedelta(days=7)) < timedelta(seconds=5)
    assert query.parse_since(None) is None


def test_scores_create_list_and_summaries(env):
    client, auth = env
    created = client.post(
        "/api/v1/scores",
        json={
            "trace_id": AGENT_TRACE_ID,
            "name": "quality",
            "value": 0.5,
            "source": "human",
            "comment": "meh",
        },
        headers=auth,
    )
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {
        "id",
        "trace_id",
        "span_id",
        "name",
        "value",
        "label",
        "comment",
        "source",
        "created_at",
    }
    assert body["created_at"].endswith("Z") and body["span_id"] is None

    client.post(
        "/api/v1/scores",
        json={
            "trace_id": AGENT_TRACE_ID,
            "span_id": AGENT_ID,
            "name": "quality",
            "value": 0.9,
            "source": "llm",
        },
        headers=auth,
    )
    client.post(
        "/api/v1/scores",
        json={"trace_id": AGENT_TRACE_ID, "name": "safe", "value": 1, "label": "pass", "source": "rule"},
        headers=auth,
    )

    listed = client.get(f"/api/v1/traces/{AGENT_TRACE_ID}/scores", headers=auth).json()
    assert [s["name"] for s in listed] == ["quality", "quality", "safe"]

    items = {t["trace_id"]: t for t in client.get("/api/v1/traces", headers=auth).json()["items"]}
    assert items[AGENT_TRACE_ID]["scores"] == {"quality": 0.9, "safe": 1.0}
    assert items[ERROR_TRACE_ID]["scores"] == {}
    detail = client.get(f"/api/v1/traces/{AGENT_TRACE_ID}", headers=auth).json()
    assert detail["trace"]["scores"] == {"quality": 0.9, "safe": 1.0}
    assert client.get(f"/api/v1/traces/{ERROR_TRACE_ID}/scores", headers=auth).json() == []


def test_score_validation_and_404(env):
    client, auth = env

    def post(**over):
        data = {"trace_id": AGENT_TRACE_ID, "name": "q", "value": 1.0, "source": "sdk", **over}
        return client.post("/api/v1/scores", json=data, headers=auth)

    assert post().status_code == 201
    assert post(source="bogus").status_code == 422
    assert post(name="").status_code == 422
    assert post(name="x" * 201).status_code == 422
    assert post(value="abc").status_code == 422
    assert post(trace_id="f" * 32).status_code == 404
    assert post(span_id="deadbeefdeadbeef").status_code == 404
    raw = client.post(
        "/api/v1/scores",
        content=f'{{"trace_id":"{AGENT_TRACE_ID}","name":"q","value":NaN,"source":"sdk"}}',
        headers={**auth, "Content-Type": "application/json"},
    )
    assert raw.status_code == 422
    assert not math.isnan(
        client.get(f"/api/v1/traces/{AGENT_TRACE_ID}/scores", headers=auth).json()[0]["value"]
    )


def test_score_hooks_called_and_failures_swallowed(env):
    client, auth = env
    calls = []

    def good(db, project_id, score):
        calls.append((project_id, score.name))

    def bad(db, project_id, score):
        raise RuntimeError("boom")

    query.on_score_created[:] = [bad, good]
    try:
        resp = client.post(
            "/api/v1/scores",
            json={"trace_id": AGENT_TRACE_ID, "name": "h", "value": 1, "source": "rule"},
            headers=auth,
        )
    finally:
        query.on_score_created.clear()
    assert resp.status_code == 201
    assert len(calls) == 1 and calls[0][1] == "h"


def test_project_isolation(env):
    client, auth = env
    other = _project(client, "two")
    client.post(
        "/api/v1/scores",
        json={"trace_id": AGENT_TRACE_ID, "name": "q", "value": 1, "source": "human"},
        headers=auth,
    )

    assert client.get("/api/v1/traces", headers=other).json()["items"] == []
    assert client.get(f"/api/v1/traces/{AGENT_TRACE_ID}", headers=other).status_code == 404
    assert client.get("/api/v1/sessions", headers=other).json()["items"] == []
    assert client.get("/api/v1/sessions/sess-2", headers=other).status_code == 404
    assert client.get("/api/v1/stats/overview", headers=other).json()["trace_count"] == 0
    assert client.get(f"/api/v1/traces/{AGENT_TRACE_ID}/scores", headers=other).json() == []
    denied = client.post(
        "/api/v1/scores",
        json={"trace_id": AGENT_TRACE_ID, "name": "q", "value": 0, "source": "human"},
        headers=other,
    )
    assert denied.status_code == 404
    assert len(client.get(f"/api/v1/traces/{AGENT_TRACE_ID}/scores", headers=auth).json()) == 1
