from datetime import timedelta

import pytest
from sqlalchemy import select, update
from test_datasets_api import ADMIN, db_url, make_client  # noqa: F401  (db_url is a fixture)

from matrix_server.evals import providers
from matrix_server.evals import runner as eval_runner
from matrix_server.evals.providers import Completion, ProviderError
from matrix_server.storage import jobs
from matrix_server.storage.db import utcnow
from matrix_server.storage.models import Experiment, ExperimentResult, Job, PromptVersion
from matrix_server.workers import runner

runner.load_handler_modules()

METRICS = [
    {"type": "equals"},
    {"type": "contains"},
    {"type": "faithfulness"},
    {"type": "answer_relevancy"},
    {"type": "latency", "params": {"max_ms": 10_000}},
]
ITEMS = [
    {
        "input": {"question": "When does order 42 ship?"},
        "expected_output": "order 42",
        "context": ["Order 42 ships tomorrow from the main warehouse."],
    },
    {
        "input": {"question": "What is the return window?"},
        "expected_output": "return window",
        "context": ["The return window is 7 days after delivery."],
    },
]


@pytest.fixture
def env(db_url, monkeypatch):  # noqa: F811
    monkeypatch.delenv("MATRIX_JUDGE_PROVIDER", raising=False)
    client = make_client(db_url)
    resp = client.post("/api/v1/admin/projects", json={"name": "one"}, headers=ADMIN)
    auth = {"Authorization": f"Bearer {resp.json()['api_key']}"}
    project_id = resp.json()["project"]["id"]
    other = client.post("/api/v1/admin/projects", json={"name": "two"}, headers=ADMIN).json()
    ds = client.post("/api/v1/datasets", json={"name": "golden"}, headers=auth).json()
    for item in ITEMS:
        assert client.post(f"/api/v1/datasets/{ds['id']}/items", json=item, headers=auth).status_code == 201
    with client.app.state.session_factory() as s, s.begin():
        s.add(PromptVersion(
            project_id=project_id, name="qa", version=1, type="chat",
            template=[{"role": "system", "content": "Be brief."}, {"role": "user", "content": "{{question}}"}],
            variables=["question"],
        ))
    yield {
        "client": client,
        "auth": auth,
        "other": {"Authorization": f"Bearer {other['api_key']}"},
        "dataset_id": ds["id"],
        "project_id": project_id,
    }
    client.app.state.db_engine.dispose()


def _variants(prompts=None, models=("model-a", "model-b"), provider="fake"):
    prompts = prompts or {
        "inline": {"template": "Answer briefly: {{question}}", "type": "text"},
        "registry": {"name": "qa", "version": 1},
    }
    return [
        {"label": f"{p}/{m}", "prompt": prompt, "model": {"provider": provider, "name": m, "params": {}}}
        for p, prompt in prompts.items()
        for m in models
    ]


def _create(env, **overrides):
    body = {"name": "exp", "dataset_id": env["dataset_id"], "variants": _variants(), "metrics": METRICS}
    body.update(overrides)
    return env["client"].post("/api/v1/experiments", json=body, headers=env["auth"])


def _drain(env) -> int:
    factory = env["client"].app.state.session_factory
    n = 0
    while runner.run_once(factory, "test-worker"):
        n += 1
    return n


def _get(env, exp_id):
    resp = env["client"].get(f"/api/v1/experiments/{exp_id}", headers=env["auth"])
    assert resp.status_code == 200
    return resp.json()


def _run(env, **overrides):
    resp = _create(env, **overrides)
    assert resp.status_code == 201, resp.text
    exp_id = resp.json()["id"]
    _drain(env)
    return _get(env, exp_id)


def test_validation_errors(env):
    client, auth = env["client"], env["auth"]

    def err(**overrides):
        resp = _create(env, **overrides)
        assert resp.status_code == 422, resp.text
        return str(resp.json()["detail"])

    assert client.post("/api/v1/experiments", json={}).status_code == 401
    assert _create(env, dataset_id="nope").status_code == 404
    assert _create(env, dataset_id=env["dataset_id"]).status_code == 201
    empty = client.post("/api/v1/datasets", json={"name": "empty"}, headers=auth).json()
    assert "no items" in err(dataset_id=empty["id"])
    err(variants=[])
    err(variants=_variants(models=[f"m{i}" for i in range(6)]))  # 12 variants
    dup = _variants()
    dup[1]["label"] = dup[0]["label"]
    assert "duplicate variant label" in err(variants=dup)
    assert "not found" in err(variants=_variants(prompts={"x": {"name": "qa", "version": 9}}))
    assert "not found" in err(variants=_variants(prompts={"x": {"name": "missing", "version": 1}}))
    assert "type" in err(variants=_variants(prompts={"x": {"template": "hi", "type": "image"}}))
    assert "chat template" in err(variants=_variants(prompts={"x": {"template": "hi", "type": "chat"}}))
    assert "unknown provider" in err(variants=_variants(provider="nope"))
    bad_params = _variants()
    bad_params[0]["model"]["params"] = {"messages": []}
    assert "must not set" in err(variants=bad_params)
    assert "unknown metric type" in err(metrics=[{"type": "bogus"}])
    assert "threshold" in err(metrics=[{"type": "faithfulness", "threshold": 3}])
    assert "duplicate metric name" in err(metrics=[{"type": "contains"}, {"type": "contains"}])
    assert "baseline" in err(baseline_experiment_id="nope")
    queued = _create(env).json()["id"]
    assert "baseline" in err(baseline_experiment_id=queued)  # not done yet
    # Nothing is visible from another project.
    assert client.get(f"/api/v1/experiments/{queued}", headers=env["other"]).status_code == 404
    assert client.get("/api/v1/experiments", headers=env["other"]).json()["items"] == []
    other_ds = client.post("/api/v1/datasets", json={"name": "x"}, headers=env["other"]).json()
    assert _create(env, dataset_id=other_ds["id"]).status_code == 404


def test_end_to_end_matrix(env):
    resp = _create(env)
    assert resp.status_code == 201 and resp.json()["status"] == "queued"
    exp_id = resp.json()["id"]
    with env["client"].app.state.session_factory() as s:
        queued = s.scalars(select(Job).where(Job.kind == "experiment.run")).all()
        assert [j.payload for j in queued] == [{"experiment_id": exp_id}]
    assert _drain(env) == 1

    body = _get(env, exp_id)
    exp = body["experiment"]
    assert exp["status"] == "done" and exp["error"] is None and exp["finished_at"] and exp["started_at"]
    labels = {v["label"] for v in exp["variants"]}
    assert len(labels) == 4 and len(body["results"]) == 4 * len(ITEMS)
    metric_names = {"equals", "contains", "faithfulness", "answer_relevancy", "latency"}
    for r in body["results"]:
        assert set(r) == {"item_id", "variant", "output", "latency_ms", "cost_usd", "input_tokens",
                          "output_tokens", "error", "scores"}
        assert r["error"] is None and r["output"] and r["latency_ms"] > 0 and r["output_tokens"] > 0
        assert set(r["scores"]) == metric_names
        assert all(set(s) == {"value", "passed", "reason"} for s in r["scores"].values())
        assert r["scores"]["contains"]["passed"] is True  # fake echoes the question
        assert r["scores"]["equals"]["passed"] is False
        assert r["scores"]["latency"]["passed"] is True
        assert 0 <= r["scores"]["faithfulness"]["value"] <= 1
        assert 0 <= r["scores"]["answer_relevancy"]["value"] <= 1
    assert set(body["summary"]) == labels
    for per_metric in body["summary"].values():
        assert set(per_metric) == metric_names
        assert per_metric["contains"] == {"mean": 1.0, "pass_rate": 1.0}
        assert per_metric["equals"] == {"mean": 0.0, "pass_rate": 0.0}
    assert body["regressions"] == []
    # Models differ in output prefix; the registry chat prompt carries the system message.
    outputs = {r["variant"]: r["output"] for r in body["results"]}
    assert outputs["inline/model-a"].split(":")[0] != outputs["inline/model-b"].split(":")[0]

    listed = env["client"].get("/api/v1/experiments", headers=env["auth"]).json()["items"]
    assert listed[0]["id"] == exp_id and listed[0]["summary"] == body["summary"]
    assert set(listed[0]) == {"id", "name", "dataset_id", "status", "created_at", "finished_at", "summary"}


def test_baseline_regressions(env):
    good = {"p": {"template": "{{question}}", "type": "text"}}
    baseline = _run(env, variants=_variants(prompts=good, models=["m"]))
    assert baseline["experiment"]["status"] == "done"
    # `name` is not in the items: rendered empty and recorded, so `contains` now fails.
    bad = {"p": {"template": [{"role": "user", "content": "Greet {{name}}"}], "type": "chat"}}
    current = _run(env, variants=_variants(prompts=bad, models=["m"]),
                   baseline_experiment_id=baseline["experiment"]["id"])
    assert current["experiment"]["status"] == "done"
    assert all("missing variables: name" in r["error"] for r in current["results"])
    regs = current["regressions"]
    contains = [r for r in regs if r["metric"] == "contains"]
    assert len(contains) == len(ITEMS)
    assert all(r["variant"] == "p/m" and r["baseline"] == 1.0 and r["current"] == 0.0 for r in contains)
    assert {r["item_id"] for r in contains} == {r["item_id"] for r in current["results"]}
    assert all(set(r) == {"item_id", "variant", "metric", "baseline", "current"} for r in regs)


def test_provider_error_in_one_cell(env, monkeypatch):
    class Flaky:
        def complete(self, messages, model, params=None):
            text = messages[-1]["content"]
            if "return window" in text:
                raise ProviderError(503, "flaky", "upstream overloaded")
            if "order 42" in text:
                return Completion(text=f"ok {text}", input_tokens=3, output_tokens=4, latency_ms=12.0,
                                  cost_usd=0.001, model=model)
            raise RuntimeError("secret detail that must not be stored")

    monkeypatch.setitem(providers._PROVIDERS, "flaky", lambda **_: Flaky())
    body = _run(env, variants=_variants(prompts={"p": {"template": "{{question}}", "type": "text"}},
                                        models=["m"], provider="flaky"))
    assert body["experiment"]["status"] == "done"
    by_q = {r["output"] is not None: r for r in body["results"]}
    ok, failed = by_q[True], by_q[False]
    assert ok["error"] is None and ok["cost_usd"] == 0.001 and ok["latency_ms"] == 12.0
    assert "503" in failed["error"] and "upstream overloaded" in failed["error"]
    assert all(s["value"] is None and s["passed"] is None for s in failed["scores"].values())
    summary = body["summary"]["p/m"]
    assert summary["contains"] == {"mean": 1.0, "pass_rate": 1.0}  # only the non-null cell counts

    # A non-provider exception in a cell keeps only the type name.
    env["client"].post(f"/api/v1/datasets/{env['dataset_id']}/items", json={"input": {"question": "x"}},
                       headers=env["auth"])
    body = _run(env, variants=_variants(prompts={"p": {"template": "{{question}}", "type": "text"}},
                                        models=["m"], provider="flaky"))
    errors = [r["error"] for r in body["results"] if r["error"]]
    assert "provider call failed: RuntimeError" in errors
    assert not any("secret" in e for e in errors)


def test_retry_is_idempotent(env, monkeypatch):
    calls = {"n": 0}
    real = eval_runner.build_eval_deps

    def flaky_deps():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient with secret sk-abcdef123456")
        return real()

    monkeypatch.setattr(eval_runner, "build_eval_deps", flaky_deps)
    resp = _create(env)
    exp_id = resp.json()["id"]
    factory = env["client"].app.state.session_factory
    assert runner.run_once(factory, "w")  # first attempt fails systemically
    first = _get(env, exp_id)
    assert first["experiment"]["status"] == "queued"
    assert first["experiment"]["error"] == "internal error: RuntimeError"
    assert first["results"] == []
    with factory() as s, s.begin():
        s.execute(update(Job).values(run_after=utcnow() - timedelta(seconds=1)))
    _drain(env)
    done = _get(env, exp_id)
    assert done["experiment"]["status"] == "done" and done["experiment"]["error"] is None
    assert len(done["results"]) == 4 * len(ITEMS)

    with factory() as s:
        ids_before = {(r.item_id, r.variant): r.id for r in s.scalars(select(ExperimentResult))}
    # Duplicate delivery of a finished experiment is a no-op.
    with factory() as s, s.begin():
        jobs.enqueue(s, env["project_id"], "experiment.run", {"experiment_id": exp_id})
    assert _drain(env) == 1
    # Re-running (e.g. lease expired mid-commit) upserts in place instead of duplicating rows.
    with factory() as s, s.begin():
        s.execute(update(Experiment).values(status="running"))
        jobs.enqueue(s, env["project_id"], "experiment.run", {"experiment_id": exp_id})
    _drain(env)
    with factory() as s:
        ids_after = {(r.item_id, r.variant): r.id for r in s.scalars(select(ExperimentResult))}
    assert ids_after == ids_before
    assert _get(env, exp_id)["summary"] == done["summary"]


def test_final_attempt_marks_failed(env, monkeypatch):
    def broken():
        raise eval_runner.ExperimentConfigError("MATRIX_JUDGE_MODEL must be set")

    monkeypatch.setattr(eval_runner, "build_eval_deps", broken)
    exp_id = _create(env).json()["id"]
    factory = env["client"].app.state.session_factory
    for _ in range(3):
        with factory() as s, s.begin():
            s.execute(update(Job).values(run_after=utcnow() - timedelta(seconds=1)))
        runner.run_once(factory, "w")
    exp = _get(env, exp_id)["experiment"]
    assert exp["status"] == "failed" and exp["error"] == "MATRIX_JUDGE_MODEL must be set"
    with factory() as s:
        assert s.scalars(select(Job.status)).all() == ["failed"]


def test_render_messages():
    msgs, missing = eval_runner.render_messages("text", "Q: {{question}} {{ctx}}", {"question": "hi"})
    assert msgs == [{"role": "user", "content": "Q: hi "}] and missing == ["ctx"]
    msgs, missing = eval_runner.render_messages("text", "Be nice.", {"question": "hi"})
    assert msgs == [{"role": "system", "content": "Be nice."}, {"role": "user", "content": "hi"}]
    chat = [{"role": "system", "content": "Sys"}]
    msgs, _ = eval_runner.render_messages("chat", chat, {"a": 1, "b": [2]})
    assert msgs[-1] == {"role": "user", "content": '{"a": 1, "b": [2]}'}
    msgs, _ = eval_runner.render_messages("chat", [{"role": "user", "content": "{{b}}"}], {"b": [2]})
    assert msgs == [{"role": "user", "content": "[2]"}]
