"""`runs.reaper`: stale runs fail with "lost heartbeat", idempotently; at most one reaper queued."""

import os
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from matrix_server.runs import service
from matrix_server.runs.models import Run
from matrix_server.storage.db import Base, as_utc, init_db, make_engine, make_session_factory, utcnow
from matrix_server.storage.models import Job, Project
from matrix_server.workers import run_handlers, runner

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


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
    runner.load_handler_modules(["matrix_server.workers.run_handlers"])
    yield make_session_factory(eng)
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


def _runs(factory, **ages):
    """One run per name; value = minutes since its last heartbeat (None = never, started 10 min ago)."""
    now = utcnow()
    with factory() as s, s.begin():
        s.add(Project(id="p1", name="p1"))
        s.flush()
        for name, age in ages.items():
            s.add(Run(id=name, project_id="p1", name=name, status="running", tags=[], params={}, key_counts={},
                      point_count=0, started_at=now - timedelta(minutes=10),
                      heartbeat_at=None if age is None else now - timedelta(minutes=age)))


def _status(factory):
    with factory() as s:
        return {r.id: (r.status, r.error) for r in s.scalars(select(Run))}


def _reapers(factory):
    with factory() as s:
        return s.scalars(select(Job).where(Job.kind == service.REAPER_KIND).order_by(Job.created_at)).all()


def test_reap_marks_stale_runs_failed_and_is_idempotent(factory):
    _runs(factory, fresh=1, stale=6, never=None)
    with factory() as s, s.begin():
        s.execute(update(Run).where(Run.id == "fresh").values(heartbeat_at=utcnow()))
        s.add(Run(id="done", project_id="p1", name="done", status="finished", tags=[], params={}, key_counts={},
                  point_count=0, started_at=utcnow() - timedelta(hours=1), heartbeat_at=utcnow() - timedelta(hours=1)))
    with factory() as s, s.begin():
        assert service.reap_stale_runs(s) == 2
    with factory() as s, s.begin():
        assert service.reap_stale_runs(s) == 0
    status = _status(factory)
    assert status["stale"] == status["never"] == ("failed", service.LOST_HEARTBEAT)
    assert status["fresh"] == ("running", None) and status["done"] == ("finished", None)


def test_reaper_job_reschedules_itself_once(factory):
    _runs(factory, stale=6)
    with factory() as s, s.begin():
        assert service.ensure_reaper_scheduled(s) is True
    with factory() as s, s.begin():
        assert service.ensure_reaper_scheduled(s) is False  # dedup: one already queued
    assert runner.run_once(factory, "w")
    assert _status(factory)["stale"][0] == "failed"
    jobs = _reapers(factory)
    assert [j.status for j in jobs] == ["done", "queued"]
    assert as_utc(jobs[1].run_after) > utcnow() + timedelta(seconds=30)
    # Delivered twice (lease lost and re-run): still a single queued successor.
    with factory() as s:
        run_handlers.reap(s, jobs[0])
        s.commit()
    assert [j.status for j in _reapers(factory)] == ["done", "queued"]
    # Run the successor: it queues the next one and nothing else changes.
    with factory() as s, s.begin():
        s.execute(update(Job).where(Job.status == "queued").values(run_after=utcnow() - timedelta(seconds=1)))
    assert runner.run_once(factory, "w")
    assert [j.status for j in _reapers(factory)] == ["done", "done", "queued"]


def test_failed_reaper_rearms_the_chain(factory):
    with factory() as s, s.begin():
        service.ensure_reaper_scheduled(s)
    job = _reapers(factory)[0]
    with factory() as s, s.begin():
        s.execute(update(Job).where(Job.id == job.id).values(status="failed"))
    run_handlers.rearm_reaper(factory, job)
    assert [j.status for j in _reapers(factory)] == ["failed", "queued"]


def test_heartbeat_revives_a_reaped_run(factory):
    from fastapi.testclient import TestClient

    from matrix_server.app import create_app
    from matrix_server.config import Settings

    url = str(factory.kw["bind"].url.render_as_string(hide_password=False))
    app = create_app(Settings(database_url=url, admin_token="adm"))
    with TestClient(app) as client:
        resp = client.post("/api/v1/admin/projects", json={"name": "rev"}, headers={"Authorization": "Bearer adm"})
        key = {"Authorization": f"Bearer {resp.json()['api_key']}"}
        rid = client.post("/api/v1/runs", json={}, headers=key).json()["id"]
        with factory() as s, s.begin():
            s.execute(update(Run).where(Run.id == rid).values(heartbeat_at=utcnow() - timedelta(minutes=6)))
            service.reap_stale_runs(s)
        assert client.get(f"/api/v1/runs/{rid}", headers=key).json()["status"] == "failed"
        assert client.post(f"/api/v1/runs/{rid}/heartbeat", headers=key).status_code == 204
        run = client.get(f"/api/v1/runs/{rid}", headers=key).json()
        assert (run["status"], run["error"], run["ended_at"]) == ("running", None, None)
        # POST /runs queued the first reaper; creating more runs does not queue another.
        client.post("/api/v1/runs", json={}, headers=key)
    app.state.db_engine.dispose()
    assert [j.status for j in _reapers(factory)] == ["queued"]
