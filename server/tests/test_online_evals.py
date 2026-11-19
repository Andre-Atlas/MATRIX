import os
import uuid

import pytest
from fastapi.testclient import TestClient
from otlp_payloads import (
    AGENT_ID,
    AGENT_TRACE_ID,
    ERROR_ID,
    ERROR_TRACE_ID,
    GENAI_ID,
    GENAI_TRACE_ID,
    RETRIEVER_ID,
    agent_trace_spans,
    export_request,
    full_request,
)
from rbac_helpers import owner_project
from sqlalchemy import select, update

from matrix_server import online_evals
from matrix_server.api import query
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.domain.spans import Span
from matrix_server.online_evals import JOB_KIND, make_ingest_hook, should_sample
from matrix_server.storage.db import Base, make_engine
from matrix_server.storage.models import Job, OnlineEval, Project, Score
from matrix_server.workers import online_handlers, runner

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
ADMIN = {"Authorization": "Bearer adm"}
PROTO = {"Content-Type": "application/x-protobuf"}


# --------------------------------------------------------------------------- sampling


def test_sampling_distribution_matches_rate():
    ids = [uuid.uuid4().hex for _ in range(10_000)]
    hits = sum(should_sample(t, "eval-1", 0.1) for t in ids)
    assert abs(hits / len(ids) - 0.1) <= 0.015


def test_sampling_edges_and_stability():
    ids = [uuid.uuid4().hex for _ in range(500)]
    assert not any(should_sample(t, "e", 0.0) for t in ids)
    assert all(should_sample(t, "e", 1.0) for t in ids)
    first = [should_sample(t, "e", 0.3) for t in ids]
    assert first == [should_sample(t, "e", 0.3) for t in ids]
    # monotonic in the rate: a trace sampled at 0.3 stays sampled at 0.6
    assert all(should_sample(t, "e", 0.6) for t, hit in zip(ids, first, strict=True) if hit)
    # independent per online eval
    assert first != [should_sample(t, "other", 0.3) for t in ids]


# --------------------------------------------------------------------------- fixtures


def _project(client, name):
    # Owner session: rules, online evals and the production label are admin-only (G0 RBAC).
    return owner_project(client, name, ADMIN)


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path}/m.db"
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        url = PG_URL
        eng = make_engine(url)
        Base.metadata.drop_all(eng)
        eng.dispose()
    monkeypatch.delenv("MATRIX_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("MATRIX_JUDGE_MODEL", raising=False)
    app = create_app(Settings(database_url=url, admin_token="adm"))
    # create_app already wires the online-evals router and ingest hook.
    online_handlers.set_span_store(app.state.span_store)
    monkeypatch.setitem(runner.HANDLERS, JOB_KIND, online_handlers.handle_online_eval_score)
    online_evals.invalidate_cache()
    client = TestClient(app)
    try:
        yield app, client, _project(client, "one")
    finally:
        online_handlers.set_span_store(None)
        online_evals.invalidate_cache()
        app.state.db_engine.dispose()
        if request.param == "postgres":
            eng = make_engine(url)
            Base.metadata.drop_all(eng)
            eng.dispose()


@pytest.fixture
def score_hook_calls():
    calls = []

    def record(session, project_id, score):
        calls.append((project_id, score.trace_id, score.span_id, score.name, score.value))

    query.on_score_created.append(record)
    yield calls
    query.on_score_created.remove(record)


def _create(client, auth, **body):
    body = {"name": "quality", "metric": "contains", "params": {"value": "ship"}, "sampling_rate": 1.0, **body}
    return client.post("/api/v1/online-evals", json=body, headers=auth)


def _ingest(client, auth, request=None):
    resp = client.post("/v1/traces", content=(request or full_request()).SerializeToString(),
                       headers={**PROTO, **auth})
    assert resp.status_code == 200


def _jobs(app):
    with app.state.session_factory() as s:
        return [j.payload for j in s.scalars(select(Job).where(Job.kind == JOB_KIND).order_by(Job.created_at))]


def _scores(app):
    with app.state.session_factory() as s:
        return list(s.scalars(select(Score).order_by(Score.trace_id)))


def _drain(app):
    n = 0
    while runner.run_once(app.state.session_factory, "w1"):
        n += 1
    return n


# --------------------------------------------------------------------------- API


def test_create_list_delete(env):
    _, client, auth = env
    resp = _create(client, auth, filter={"kind": "LLM", "name": "x"}, model={"provider": "anthropic", "name": "m"})
    assert resp.status_code == 201
    created = resp.json()
    assert created["filter"] == {"kind": "LLM", "name": "x"}
    assert created["model"] == {"provider": "anthropic", "name": "m"}
    assert created["enabled"] is True and created["created_at"].endswith("Z")
    items = client.get("/api/v1/online-evals", headers=auth).json()["items"]
    assert [i["id"] for i in items] == [created["id"]]
    assert _create(client, auth).status_code == 409
    assert client.delete(f"/api/v1/online-evals/{created['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/online-evals", headers=auth).json()["items"] == []
    assert client.delete(f"/api/v1/online-evals/{created['id']}", headers=auth).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"metric": "nope"},
        {"metric": "g_eval", "params": {}},
        {"metric": "contains", "params": {"bogus": 1}},
        {"sampling_rate": 1.5},
        {"sampling_rate": -0.1},
        {"sampling_rate": None},
        {"filter": {"kind": "NOT_A_KIND"}},
        {"filter": {"other": "x"}},
        {"model": {"provider": "nope", "name": "m"}},
        {"model": {"provider": "openai", "name": "m", "api_key": "sk-secret"}},
        {"model": {"provider": "openai", "name": "m", "base_url": "http://169.254.169.254"}},
        {"name": ""},
    ],
)
def test_validation(env, body):
    _, client, auth = env
    resp = _create(client, auth, **body)
    assert resp.status_code == 422
    assert "sk-secret" not in resp.text


def test_project_isolation(env):
    _, client, auth = env
    other = _project(client, "two")
    ev = _create(client, auth).json()
    assert client.get("/api/v1/online-evals", headers=other).json()["items"] == []
    assert client.delete(f"/api/v1/online-evals/{ev['id']}", headers=other).status_code == 404
    assert _create(client, other).status_code == 201  # names are per project
    assert client.get("/api/v1/online-evals").status_code == 401


# --------------------------------------------------------------------------- ingest hook


def test_hook_enqueues_root_spans_by_default(env):
    app, client, auth = env
    ev = _create(client, auth).json()
    _ingest(client, auth)
    jobs = _jobs(app)
    assert {(j["trace_id"], j["span_id"]) for j in jobs} == {
        (AGENT_TRACE_ID, AGENT_ID), (GENAI_TRACE_ID, GENAI_ID), (ERROR_TRACE_ID, ERROR_ID)
    }
    assert all(j["online_eval_id"] == ev["id"] for j in jobs)


def test_hook_filters_by_kind_and_name(env):
    app, client, auth = env
    _create(client, auth, name="ret", filter={"kind": "RETRIEVER"})
    _create(client, auth, name="named", filter={"name": "pipeline"})
    _create(client, auth, name="never", sampling_rate=0.0)
    _ingest(client, auth)
    assert sorted((j["trace_id"], j["span_id"]) for j in _jobs(app)) == sorted(
        [(AGENT_TRACE_ID, RETRIEVER_ID), (ERROR_TRACE_ID, ERROR_ID)]
    )


def test_hook_enqueues_only_sampled_traces(env):
    app, client, auth = env
    ev = _create(client, auth, sampling_rate=0.5).json()
    trace_ids = [uuid.uuid4().hex for _ in range(40)]
    spans = [s for t in trace_ids for s in agent_trace_spans(t)]
    _ingest(client, auth, export_request(spans))
    expected = {t for t in trace_ids if should_sample(t, ev["id"], 0.5)}
    assert 0 < len(expected) < 40
    assert {j["trace_id"] for j in _jobs(app)} == expected


def test_hook_skips_disabled_and_other_projects(env):
    app, client, auth = env
    ev = _create(client, auth).json()
    with app.state.session_factory() as s, s.begin():
        s.execute(update(OnlineEval).where(OnlineEval.id == ev["id"]).values(enabled=False))
    online_evals.invalidate_cache()
    _ingest(client, auth)
    _ingest(client, _project(client, "two"))
    assert _jobs(app) == []


def test_hook_never_raises():
    def broken_factory():
        raise RuntimeError("db down")

    hook = make_ingest_hook(broken_factory)
    span = Span.model_validate(
        {"span_id": "s", "trace_id": "t", "name": "n", "start_time": "2026-01-01T00:00:00Z",
         "end_time": "2026-01-01T00:00:01Z"}
    )
    assert hook("p", [span]) is None


# --------------------------------------------------------------------------- worker


def test_worker_scores_and_calls_hooks(env, score_hook_calls):
    app, client, auth = env
    _create(client, auth)  # contains "ship": agent root output "Your order ships tomorrow."
    assert _create(client, auth, name="faith", metric="faithfulness", params={}).status_code == 201
    _ingest(client, auth)
    assert _drain(app) == 6
    scores = {(s.name, s.trace_id): s for s in _scores(app)}
    agent = scores[("quality", AGENT_TRACE_ID)]
    assert agent.value == 1.0 and agent.span_id == AGENT_ID and agent.source == "llm"
    assert scores[("quality", GENAI_TRACE_ID)].value == 0.0
    # faithfulness needs contexts: only the agent trace (with a retriever) gets a score
    assert {t for (n, t) in scores if n == "faith"} == {AGENT_TRACE_ID}
    faith = scores[("faith", AGENT_TRACE_ID)]
    assert 0.0 <= faith.value <= 1.0 and faith.comment and len(faith.comment) <= 1000
    # Default judge (no MATRIX_JUDGE_PROVIDER) is the heuristic one: marked as such on the score.
    assert faith.source == "llm" and faith.label == "heuristic"
    assert faith.comment.startswith("[heuristic judge] ")
    # Non-judge metrics are not marked.
    assert agent.label is None and not (agent.comment or "").startswith("[heuristic judge]")
    project_id = agent.project_id
    assert sorted(c[3] for c in score_hook_calls) == ["faith", "quality", "quality", "quality"]
    assert all(c[0] == project_id for c in score_hook_calls)
    with app.state.session_factory() as s:
        assert {j.status for j in s.scalars(select(Job))} == {"done"}


def test_worker_is_idempotent(env):
    app, client, auth = env
    _create(client, auth)
    _ingest(client, auth)
    jobs = _jobs(app)
    _drain(app)
    assert len(_scores(app)) == 3
    # retried / duplicated jobs do not write a second score
    with app.state.session_factory() as s, s.begin():
        project_id = s.scalars(select(Project.id).where(Project.name == "one")).one()
        for payload in jobs:
            s.add(Job(project_id=project_id, kind=JOB_KIND, payload=payload))
    _drain(app)
    assert len(_scores(app)) == 3
    # re-ingesting an already scored trace enqueues nothing new
    before = len(_jobs(app))
    _ingest(client, auth)
    assert len(_jobs(app)) == before


def test_worker_skips_deleted_and_disabled(env):
    app, client, auth = env
    gone = _create(client, auth).json()
    off = _create(client, auth, name="off").json()
    _ingest(client, auth)
    assert client.delete(f"/api/v1/online-evals/{gone['id']}", headers=auth).status_code == 204
    with app.state.session_factory() as s, s.begin():
        s.execute(update(OnlineEval).where(OnlineEval.id == off["id"]).values(enabled=False))
    _drain(app)
    assert _scores(app) == []
    with app.state.session_factory() as s:
        assert {j.status for j in s.scalars(select(Job))} == {"done"}


def test_failing_score_hook_keeps_the_score(env):
    app, client, auth = env

    def broken(session, project_id, score):
        session.add(Score(project_id=project_id, trace_id="x", name="partial", value=1, source="rule"))
        session.flush()
        raise RuntimeError("boom")

    query.on_score_created.append(broken)
    try:
        _create(client, auth, filter={"name": "support-agent"})
        _ingest(client, auth)
        _drain(app)
    finally:
        query.on_score_created.remove(broken)
    assert [(s.name, s.trace_id) for s in _scores(app)] == [("quality", AGENT_TRACE_ID)]


def test_build_deps_uses_env_and_model(monkeypatch):
    from matrix_server.evals import HeuristicJudge, LLMJudge

    assert isinstance(online_handlers.build_deps(None).judge, HeuristicJudge)
    monkeypatch.setenv("MATRIX_JUDGE_PROVIDER", "anthropic")
    monkeypatch.setenv("MATRIX_JUDGE_MODEL", "claude-x")
    deps = online_handlers.build_deps(None)
    assert isinstance(deps.judge, LLMJudge) and deps.judge.model == "claude-x"
    deps = online_handlers.build_deps({"provider": "openai", "name": "gpt-x"})
    assert isinstance(deps.judge, LLMJudge) and deps.judge.model == "gpt-x"
    assert isinstance(online_handlers.build_deps({"provider": "fake", "name": "f"}).judge, HeuristicJudge)


def test_llm_judge_score_has_no_heuristic_mark(env, monkeypatch):
    from matrix_server.evals import EvalDeps, HashingEmbedder, LLMJudge, get_provider

    class StubJudge(LLMJudge):
        def extract_statements(self, question, answer):
            return [answer]

        def verify(self, statement, contexts):
            return True, "supported"

    deps = EvalDeps(judge=StubJudge(get_provider("fake"), "judge-m"), embedder=HashingEmbedder())
    monkeypatch.setattr(online_handlers, "build_deps", lambda model: deps)
    app, client, auth = env
    assert _create(client, auth, name="faith", metric="faithfulness", params={}).status_code == 201
    _ingest(client, auth)
    _drain(app)
    faith = next(s for s in _scores(app) if s.name == "faith")
    assert faith.source == "llm" and faith.label is None
    assert not (faith.comment or "").startswith("[heuristic judge]")


def test_default_online_judge_warns_once(monkeypatch, caplog):
    from matrix_server.evals import runner as eval_runner

    monkeypatch.delenv("MATRIX_JUDGE_PROVIDER", raising=False)
    monkeypatch.setattr(eval_runner, "_heuristic_warned", False)
    with caplog.at_level("WARNING"):
        online_handlers.build_deps(None)
        online_handlers.build_deps(None)
        eval_runner.build_eval_deps()  # shared per-process flag
    assert [r.getMessage() for r in caplog.records].count(eval_runner.HEURISTIC_JUDGE_WARNING) == 1
