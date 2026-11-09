"""Regression tests for the security-review fixes: job lease renewal and final-failure settling,
ReDoS-safe regexes, score hooks in the worker, body limits, annotation double-complete,
online-eval score dedup and the base-URL configuration."""

import os
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from test_datasets_api import ADMIN, db_url, make_client  # noqa: F401  (db_url is a fixture)

from matrix_server.api import query
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.domain.spans import Span, SpanKind
from matrix_server.evals import runner as eval_runner
from matrix_server.evals.metrics import EvalCase, build_metric
from matrix_server.hooks import install_worker_hooks
from matrix_server.online_evals import JOB_KIND
from matrix_server.storage import jobs
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory, utcnow
from matrix_server.storage.models import (
    Alert,
    Annotation,
    DatasetItem,
    Experiment,
    ExperimentResult,
    Job,
    OnlineEval,
    PreferencePair,
    Project,
    Rule,
    Score,
)
from matrix_server.storage.spans import MemorySpanStore
from matrix_server.workers import context, handlers, online_handlers, runner

runner.load_handler_modules()
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
CATASTROPHIC = "^(a|aa)+$"  # exponential even on the `regex` engine


@pytest.fixture(params=["sqlite", "postgres"])
def factory(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'matrix.db'}")
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
def scratch_kinds():
    added: list[str] = []
    yield added
    for kind in added:
        runner.HANDLERS.pop(kind, None)
        runner.FINAL_FAILURE_HOOKS.pop(kind, None)


def _enqueue(factory, kind, payload=None, project_id="p1", **kw) -> str:
    with factory() as s, s.begin():
        return jobs.enqueue(s, project_id, kind, payload or {}, **kw).id


# --------------------------------------------------------------------------- 1. lease


def test_heartbeat_renews_only_own_running_job(factory):
    job_id = _enqueue(factory, "k")
    assert not jobs.heartbeat(factory, job_id, "w1")  # queued, not ours yet
    job = jobs.claim(factory, "w1")
    assert job.id == job_id
    with factory() as s, s.begin():
        s.execute(update(Job).values(locked_at=utcnow() - timedelta(hours=1)))
    assert jobs.heartbeat(factory, job_id, "w1")
    with factory() as s:
        locked_at = s.get(Job, job_id).locked_at
    assert locked_at.replace(tzinfo=locked_at.tzinfo or UTC) > datetime.now(UTC) - timedelta(minutes=1)
    assert not jobs.heartbeat(factory, job_id, "w2")
    jobs.complete(factory, job_id, "w1")
    assert not jobs.heartbeat(factory, job_id, "w1")


def test_long_job_keeps_its_lease_and_runs_once(factory, scratch_kinds):
    calls = []

    @runner.register("slow")
    def slow(session, job):
        calls.append(job.attempts)
        time.sleep(1.2)

    scratch_kinds.append("slow")
    job_id = _enqueue(factory, "slow")

    def work(name):
        for _ in range(5):
            runner.run_once(factory, name, lease_seconds=0.4)
            time.sleep(0.1)

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with factory() as s:
        job = s.get(Job, job_id)
    assert calls == [1]
    assert job.status == "done"


def test_lost_lease_sets_flag_and_discards_result(factory, scratch_kinds):
    seen = {}

    @runner.register("stolen")
    def stolen(session, job):
        with factory() as other, other.begin():  # another worker reclaims the job
            other.execute(update(Job).where(Job.id == job.id).values(locked_by="thief"))
        deadline = time.monotonic() + 5
        while not context.job_lost() and time.monotonic() < deadline:
            time.sleep(0.02)
        seen["lost"] = context.job_lost()
        session.add(Score(project_id="p1", trace_id="t", name="x", value=1.0, source="sdk"))
        session.flush()

    scratch_kinds.append("stolen")
    job_id = _enqueue(factory, "stolen")
    assert runner.run_once(factory, "w1", lease_seconds=0.3)
    assert seen["lost"] is True
    with factory() as s:
        assert s.scalar(select(func.count(Score.id))) == 0
        job = s.get(Job, job_id)
        assert job.status == "running" and job.locked_by == "thief"  # left to its new owner


def test_final_failure_hooks_run_on_last_attempt_and_on_expired_lease(factory, scratch_kinds):
    settled = []

    @runner.register("doomed")
    def doomed(session, job):
        raise RuntimeError("boom")

    @runner.on_final_failure("doomed")
    def settle(session_factory, job):
        settled.append((job.id, job.error.splitlines()[0]))

    scratch_kinds.append("doomed")
    first = _enqueue(factory, "doomed", max_attempts=1)
    assert runner.run_once(factory, "w1")
    assert settled == [(first, "RuntimeError: boom")]

    # A running job on its last attempt whose worker vanished: the next claim settles it.
    second = _enqueue(factory, "doomed", max_attempts=1)
    with factory() as s, s.begin():
        s.execute(
            update(Job)
            .where(Job.id == second)
            .values(status="running", attempts=1, locked_by="dead", locked_at=utcnow() - timedelta(hours=1))
        )
    assert not runner.run_once(factory, "w2", lease_seconds=60)
    assert settled[-1] == (second, jobs.LEASE_EXPIRED_ERROR)
    assert not runner.run_once(factory, "w3", lease_seconds=60)  # reported once
    assert len(settled) == 2


# --- experiments ------------------------------------------------------------------


ITEMS = [{"input": {"question": f"q{i}"}, "expected_output": "a", "context": []} for i in range(3)]


def _variant(label="v"):
    return {"label": label, "prompt": {"template": "Answer: {{question}}", "type": "text"},
            "model": {"provider": "fake", "name": "m"}}


@pytest.fixture
def exp_env(db_url, monkeypatch):  # noqa: F811
    monkeypatch.delenv("MATRIX_JUDGE_PROVIDER", raising=False)
    client = make_client(db_url)
    resp = client.post("/api/v1/admin/projects", json={"name": "one"}, headers=ADMIN)
    auth = {"Authorization": f"Bearer {resp.json()['api_key']}"}
    ds = client.post("/api/v1/datasets", json={"name": "golden"}, headers=auth).json()
    for item in ITEMS:
        assert client.post(f"/api/v1/datasets/{ds['id']}/items", json=item, headers=auth).status_code == 201
    yield client, auth, ds["id"]
    client.app.state.db_engine.dispose()


def _create_exp(client, auth, dataset_id, variants=None, metrics=None):
    body = {"name": "e", "dataset_id": dataset_id, "variants": variants or [_variant()],
            "metrics": metrics or [{"type": "contains"}]}
    return client.post("/api/v1/experiments", json=body, headers=auth)


def test_experiment_size_caps(exp_env, monkeypatch):
    client, auth, dataset_id = exp_env
    monkeypatch.setenv("MATRIX_MAX_EXPERIMENT_CELLS", "5")
    resp = _create_exp(client, auth, dataset_id, variants=[_variant("a"), _variant("b")])  # 3 x 2 = 6
    assert resp.status_code == 422 and "MATRIX_MAX_EXPERIMENT_CELLS" in resp.json()["detail"]
    assert _create_exp(client, auth, dataset_id).status_code == 201  # 3 x 1
    metrics = [{"type": "contains", "name": f"m{i}"} for i in range(21)]
    assert _create_exp(client, auth, dataset_id, metrics=metrics).status_code == 422
    monkeypatch.delenv("MATRIX_MAX_EXPERIMENT_CELLS")
    assert eval_runner.max_cells() == 5000


def test_expired_last_attempt_marks_experiment_failed(exp_env):
    client, auth, dataset_id = exp_env
    exp_id = _create_exp(client, auth, dataset_id).json()["id"]
    factory = client.app.state.session_factory
    with factory() as s, s.begin():  # its worker died mid-run on the last attempt
        s.execute(update(Experiment).values(status="running"))
        s.execute(update(Job).values(status="running", attempts=3, locked_by="dead",
                                     locked_at=utcnow() - timedelta(hours=1)))
    runner.run_once(factory, "w2", lease_seconds=60)
    exp = client.get(f"/api/v1/experiments/{exp_id}", headers=auth).json()["experiment"]
    assert exp["status"] == "failed"
    assert exp["error"] == handlers.LEASE_EXPIRED_MESSAGE
    assert exp["finished_at"] is not None


def test_experiment_stops_early_when_lease_lost(exp_env, monkeypatch):
    client, auth, dataset_id = exp_env
    exp_id = _create_exp(client, auth, dataset_id).json()["id"]
    factory = client.app.state.session_factory
    cells = []
    real = eval_runner.run_cell

    def counting(item, variant, metrics, deps):
        cells.append(item["id"])
        return real(item, variant, metrics, deps)

    monkeypatch.setattr(eval_runner, "run_cell", counting)
    monkeypatch.setattr(eval_runner, "concurrency", lambda: 1)
    ctx = context.JobContext(job_id="j", worker_id="w")
    ctx.lost.set()
    token = context.current_job.set(ctx)
    try:
        with factory() as s:
            exp = s.get(Experiment, exp_id)
            with pytest.raises(context.JobLost):
                eval_runner.run_experiment(s, exp)
    finally:
        context.current_job.reset(token)
    assert cells == []
    with factory() as s:
        assert s.scalar(select(func.count(ExperimentResult.id))) == 0


# --------------------------------------------------------------------------- 2. ReDoS


def test_regex_metric_times_out():
    metric = build_metric({"type": "regex", "params": {"pattern": CATASTROPHIC}})
    t0 = time.monotonic()
    result = metric.evaluate(EvalCase(input={}, output="a" * 60 + "!"))
    assert time.monotonic() - t0 < 3
    assert result.passed is False and result.reason == "regex timeout"
    assert metric.evaluate(EvalCase(input={}, output="aaaa")).passed is True


def test_is_json_pattern_uses_regex_with_shared_budget():
    schema = {"type": "array", "items": {"type": "string", "pattern": CATASTROPHIC}}
    metric = build_metric({"type": "is_json", "params": {"schema": schema}})
    bad = '["' + "a" * 60 + '!"]'
    t0 = time.monotonic()
    result = metric.evaluate(EvalCase(input={}, output="[" + ",".join([bad[1:-1]] * 5) + "]"))
    assert time.monotonic() - t0 < 3  # one budget for the whole evaluation, not one per string
    assert result.passed is False and result.reason == "regex timeout"
    assert metric.evaluate(EvalCase(input={}, output='["aa"]')).passed is True
    no = metric.evaluate(EvalCase(input={}, output='["b"]'))
    assert no.passed is False and "does not match" in no.reason


def test_is_json_rejects_pattern_properties_and_bad_patterns():
    with pytest.raises(ValueError, match="patternProperties"):
        build_metric({"type": "is_json", "params": {"schema": {"patternProperties": {"a": {}}}}})
    with pytest.raises(ValueError, match="invalid schema pattern"):
        build_metric({"type": "is_json", "params": {"schema": {"pattern": "(unclosed"}}})


# --------------------------------------------------------------------------- 3. worker hooks


def _span(project_id, trace_id, span_id, output):
    now = datetime.now(UTC)
    return Span(span_id=span_id, trace_id=trace_id, name="root", kind=SpanKind.LLM, start_time=now,
                end_time=now + timedelta(milliseconds=5), status="ok", input="When does it ship?",
                output=output, project_id=project_id)


def _worker_setup(factory):
    """What `cli.run_worker` does, without create_app."""
    store = MemorySpanStore()
    install_worker_hooks(store)
    with factory() as s, s.begin():
        project = Project(name="p")
        s.add(project)
        s.flush()
        ev = OnlineEval(project_id=project.id, name="quality", metric="contains", params={"value": "ship"})
        rule = Rule(project_id=project.id, name="low", condition={"score_name": "quality", "op": "<", "value": 0.5},
                    actions=["annotate", "alert"])
        s.add_all([ev, rule])
        s.flush()
        ids = project.id, ev.id
    store.insert([_span(ids[0], "t" * 32, "s" * 16, "no idea")])
    return store, ids


def _counts(factory):
    with factory() as s:
        return tuple(s.scalar(select(func.count(m.id))) for m in (Score, Annotation, Alert))


def test_worker_scores_fire_rules_atomically(factory, scratch_kinds):
    try:
        _, (project_id, ev_id) = _worker_setup(factory)
        payload = {"online_eval_id": ev_id, "trace_id": "t" * 32, "span_id": "s" * 16}

        @runner.register("online.then.crash")
        def crash(session, job):
            online_handlers.handle_online_eval_score(session, job)
            assert session.scalar(select(func.count(Alert.id))) == 1  # hook ran in this transaction
            raise RuntimeError("crash after scoring")

        scratch_kinds.append("online.then.crash")
        _enqueue(factory, "online.then.crash", payload, project_id=project_id, max_attempts=1)
        assert runner.run_once(factory, "w")
        assert _counts(factory) == (0, 0, 0)  # job rolled back: no score, annotation or alert

        _enqueue(factory, JOB_KIND, payload, project_id=project_id)
        assert runner.run_once(factory, "w")
        assert _counts(factory) == (1, 1, 1)
        with factory() as s:
            alert = s.scalars(select(Alert)).one()
            assert "quality=0" in alert.message
    finally:
        online_handlers.set_span_store(None)


def test_worker_hook_install_is_idempotent():
    store = MemorySpanStore()
    try:
        install_worker_hooks(store)
        install_worker_hooks(store)
        assert len(query.on_score_created) == 1
        assert online_handlers.get_span_store() is store
    finally:
        online_handlers.set_span_store(None)


def test_run_worker_installs_span_store_and_score_hook(tmp_path, monkeypatch):
    import signal

    from matrix_server import cli, hooks

    seen = {}
    monkeypatch.setattr(signal, "signal", lambda *a: None)  # keep pytest's own handlers

    def fake_run_forever(session_factory, stop_event=None, worker_id=""):
        seen["hooks"] = [getattr(h, "__qualname__", "") for h in query.on_score_created]
        seen["store"] = online_handlers.get_span_store()

    monkeypatch.setenv("MATRIX_DATABASE_URL", f"sqlite:///{tmp_path}/w.db")
    monkeypatch.setattr(runner, "run_forever", fake_run_forever)
    try:
        cli.run_worker()
        assert seen["hooks"] == ["make_score_hook.<locals>.rules_score_hook"]
        assert isinstance(seen["store"], MemorySpanStore)
    finally:
        online_handlers.set_span_store(None)
        hooks._uninstall_previous_hooks()


# --------------------------------------------------------------------------- 6. score dedup


def test_duplicate_online_jobs_record_one_score(factory, monkeypatch):
    try:
        _, (project_id, ev_id) = _worker_setup(factory)
        payload = {"online_eval_id": ev_id, "trace_id": "t" * 32, "span_id": "s" * 16}
        # Both deliveries pass the pre-check (as when two workers run them at once).
        monkeypatch.setattr(online_handlers, "_existing_score", lambda *a, **k: False)
        _enqueue(factory, JOB_KIND, payload, project_id=project_id)
        _enqueue(factory, JOB_KIND, payload, project_id=project_id)
        assert runner.run_once(factory, "w")
        assert runner.run_once(factory, "w")
        with factory() as s:
            scores = s.scalars(select(Score)).all()
            assert len(scores) == 1
            assert scores[0].dedup_key == f"online:{ev_id}:{'t' * 32}:{'s' * 16}"
            assert s.scalars(select(Job.status)).all() == ["done", "done"]
    finally:
        online_handlers.set_span_store(None)


# --------------------------------------------------------------------------- 4. body limits


@pytest.fixture
def small_client(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/m.db", admin_token="adm",
                              max_body_bytes=4000, max_json_body_bytes=1000))
    client = TestClient(app)
    resp = client.post("/api/v1/admin/projects", json={"name": "one"}, headers=ADMIN)
    yield client, {"Authorization": f"Bearer {resp.json()['api_key']}"}
    app.state.db_engine.dispose()


def test_json_api_body_limit(small_client):
    client, auth = small_client
    big = {"name": "x" * 2000}
    assert client.post("/api/v1/datasets", json=big, headers=auth).status_code == 413

    def chunks():
        yield b'{"name": "'
        for _ in range(50):
            yield b"x" * 100
        yield b'"}'

    resp = client.post("/api/v1/datasets", content=chunks(),
                       headers={**auth, "Content-Type": "application/json"})
    assert resp.status_code == 413
    assert client.post("/api/v1/datasets", json={"name": "ok"}, headers=auth).status_code == 201


def test_ingest_keeps_the_general_limit(small_client):
    client, auth = small_client
    resp = client.post("/v1/traces", content=b"\0" * 2000,
                       headers={**auth, "Content-Type": "application/x-protobuf"})
    assert resp.status_code != 413  # above the JSON limit, under max_body_bytes
    resp = client.post("/v1/traces", content=b"\0" * 5000,
                       headers={**auth, "Content-Type": "application/x-protobuf"})
    assert resp.status_code == 413


def test_text_field_caps(exp_env):
    client, auth, dataset_id = exp_env
    url = f"/api/v1/datasets/{dataset_id}/items"
    assert client.post(url, json={"input": {}, "expected_output": "x" * 100_001}, headers=auth).status_code == 422
    assert client.post(url, json={"input": {}, "context": ["x" * 50_001]}, headers=auth).status_code == 422


def test_dataset_items_are_paginated(exp_env):
    client, auth, dataset_id = exp_env
    first = client.get(f"/api/v1/datasets/{dataset_id}?limit=2", headers=auth).json()
    assert first["dataset"]["item_count"] == 3
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get(f"/api/v1/datasets/{dataset_id}?limit=2&cursor={first['next_cursor']}",
                        headers=auth).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    seen = [i["input"]["question"] for i in first["items"] + second["items"]]
    assert seen == ["q0", "q1", "q2"]
    assert client.get(f"/api/v1/datasets/{dataset_id}?limit=501", headers=auth).status_code == 422
    assert client.get(f"/api/v1/datasets/{dataset_id}?cursor=zz", headers=auth).status_code == 400


# --------------------------------------------------------------------------- 5. annotation complete


def test_concurrent_completes_create_one_pair(db_url):  # noqa: F811
    from otlp_payloads import AGENT_TRACE_ID, full_request

    client = make_client(db_url)
    try:
        resp = client.post("/api/v1/admin/projects", json={"name": "one"}, headers=ADMIN)
        auth = {"Authorization": f"Bearer {resp.json()['api_key']}"}
        assert client.post("/v1/traces", content=full_request().SerializeToString(),
                           headers={**auth, "Content-Type": "application/x-protobuf"}).status_code == 200
        ann = client.post("/api/v1/annotations", json={"trace_id": AGENT_TRACE_ID}, headers=auth).json()
        ds = client.post("/api/v1/datasets", json={"name": "d"}, headers=auth).json()
        body = {"corrected_output": "fixed", "scores": [{"name": "q", "value": 1}], "add_to_dataset_id": ds["id"]}
        barrier = threading.Barrier(4)
        codes = []

        def complete():
            barrier.wait()
            codes.append(client.post(f"/api/v1/annotations/{ann['id']}/complete", json=body,
                                     headers=auth).status_code)

        threads = [threading.Thread(target=complete) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes) == [200, 409, 409, 409]
        with client.app.state.session_factory() as s:
            assert s.scalar(select(func.count(PreferencePair.id))) == 1
            assert s.scalar(select(func.count(DatasetItem.id))) == 1
            assert s.scalar(select(func.count(Score.id))) == 1
    finally:
        client.app.state.db_engine.dispose()


# --------------------------------------------------------------------------- 7. base URL


def test_judge_base_url_falls_back_to_openai_base_url(monkeypatch):
    seen = {}

    def fake_provider(name, **opts):
        seen["provider"] = opts
        return object()

    monkeypatch.setattr(online_handlers, "get_provider", fake_provider)
    monkeypatch.setattr(online_handlers, "OpenAIEmbedder", lambda **kw: seen.setdefault("embedder", kw))
    monkeypatch.delenv("MATRIX_JUDGE_BASE_URL", raising=False)
    monkeypatch.setenv("MATRIX_OPENAI_BASE_URL", "http://llm.internal/v1")
    online_handlers.build_deps({"provider": "openai", "name": "gpt"})
    assert seen["provider"] == {"base_url": "http://llm.internal/v1"}
    assert seen["embedder"] == {"base_url": "http://llm.internal/v1"}
    monkeypatch.setenv("MATRIX_JUDGE_BASE_URL", "http://judge.internal/v1")
    online_handlers.build_deps({"provider": "openai", "name": "gpt"})
    assert seen["provider"] == {"base_url": "http://judge.internal/v1"}
