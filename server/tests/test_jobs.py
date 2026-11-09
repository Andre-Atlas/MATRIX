import os
import threading
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from matrix_server.storage import jobs
from matrix_server.storage.db import Base, as_utc, init_db, make_engine, make_session_factory, utcnow
from matrix_server.storage.models import Job, Score
from matrix_server.workers import runner

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
    yield make_session_factory(eng)
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(autouse=True)
def isolated_handlers(monkeypatch):
    monkeypatch.setattr(runner, "HANDLERS", {})


def _enqueue(factory, kind="test.ok", payload=None, **kw) -> str:
    with factory() as s, s.begin():
        return jobs.enqueue(s, "p1", kind, payload or {"n": 1}, **kw).id


def _get(factory, job_id) -> Job:
    with factory() as s:
        return s.get(Job, job_id)


def _make_runnable(factory, job_id) -> None:
    with factory() as s, s.begin():
        s.execute(update(Job).where(Job.id == job_id).values(run_after=utcnow() - timedelta(seconds=1)))


def test_enqueue_claim_complete(factory):
    job_id = _enqueue(factory, payload={"experiment_id": "e1"})
    job = jobs.claim(factory, "w1")
    assert job.id == job_id and job.status == "running" and job.locked_by == "w1"
    assert job.attempts == 1 and job.payload == {"experiment_id": "e1"}
    assert jobs.claim(factory, "w2") is None
    assert jobs.complete(factory, job_id, worker_id="w1")
    done = _get(factory, job_id)
    assert done.status == "done" and done.finished_at is not None


def test_claim_respects_run_after_order_and_kinds(factory):
    _enqueue(factory, run_after=utcnow() + timedelta(hours=1))
    first = _enqueue(factory, kind="a")
    second = _enqueue(factory, kind="b")
    assert jobs.claim(factory, "w", kinds=["b"]).id == second
    assert jobs.claim(factory, "w").id == first
    assert jobs.claim(factory, "w") is None


def test_no_double_claim_across_threads(factory):
    ids = {_enqueue(factory) for _ in range(30)}
    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def worker(n: int) -> None:
        barrier.wait()
        while (job := jobs.claim(factory, f"w{n}")) is not None:
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(claimed) == sorted(ids)
    with factory() as s:
        assert all(j.attempts == 1 for j in s.scalars(select(Job)))


def test_retry_with_backoff_then_failed(factory):
    job_id = _enqueue(factory)
    for attempt in (1, 2):
        job = jobs.claim(factory, "w")
        assert job.attempts == attempt
        before = utcnow()
        assert jobs.fail(factory, job_id, "boom " * 1000)
        failed = _get(factory, job_id)
        assert failed.status == "queued" and failed.locked_by is None
        assert len(failed.error) == jobs.MAX_ERROR_CHARS
        delay = (as_utc(failed.run_after) - before).total_seconds()
        assert 2**attempt - 1 < delay <= 2**attempt + 1
        assert jobs.claim(factory, "w") is None  # still backing off
        _make_runnable(factory, job_id)
    jobs.claim(factory, "w")
    assert jobs.fail(factory, job_id, "boom")
    final = _get(factory, job_id)
    assert final.status == "failed" and final.attempts == 3 and final.finished_at is not None
    assert jobs.claim(factory, "w") is None


def test_fail_without_retry(factory):
    job_id = _enqueue(factory)
    jobs.claim(factory, "w")
    assert jobs.fail(factory, job_id, "bad payload", retry=False)
    assert _get(factory, job_id).status == "failed"


def _expire_lease(factory, job_id) -> None:
    with factory() as s, s.begin():
        s.execute(update(Job).where(Job.id == job_id).values(locked_at=utcnow() - timedelta(seconds=600)))


def test_expired_lease_is_reclaimed(factory):
    job_id = _enqueue(factory)
    jobs.claim(factory, "w1")
    assert jobs.claim(factory, "w2") is None  # lease still valid
    _expire_lease(factory, job_id)
    job = jobs.claim(factory, "w2")
    assert job.id == job_id and job.locked_by == "w2" and job.attempts == 2
    # the original worker lost the lease and cannot finish or fail it
    assert not jobs.complete(factory, job_id, worker_id="w1")
    assert not jobs.fail(factory, job_id, "late", worker_id="w1")
    assert jobs.complete(factory, job_id, worker_id="w2")


def test_expired_lease_after_last_attempt_fails(factory):
    job_id = _enqueue(factory, max_attempts=1)
    jobs.claim(factory, "w1")
    _expire_lease(factory, job_id)
    assert jobs.claim(factory, "w2") is None
    job = _get(factory, job_id)
    assert job.status == "failed" and "lease expired" in job.error


def test_runner_dispatches_and_commits_with_done(factory):
    seen = []

    @runner.register("test.ok")
    def handle(session, job):
        seen.append(job.payload)
        session.add(Score(project_id=job.project_id, trace_id="t1", name="x", value=1.0, source="eval"))

    job_id = _enqueue(factory)
    assert runner.run_once(factory, "w")
    assert seen == [{"n": 1}]
    assert _get(factory, job_id).status == "done"
    with factory() as s:
        assert len(s.scalars(select(Score)).all()) == 1
    assert not runner.run_once(factory, "w")


def test_runner_handler_error_rolls_back_and_retries(factory):
    @runner.register("test.ok")
    def handle(session, job):
        session.add(Score(project_id=job.project_id, trace_id="t1", name="x", value=1.0, source="eval"))
        raise RuntimeError("provider down")

    job_id = _enqueue(factory)
    assert runner.run_once(factory, "w")
    job = _get(factory, job_id)
    assert job.status == "queued" and job.attempts == 1 and "provider down" in job.error
    with factory() as s:
        assert s.scalars(select(Score)).all() == []


def test_runner_unknown_kind_fails_without_retry(factory):
    job_id = _enqueue(factory, kind="nope")
    assert runner.run_once(factory, "w")
    job = _get(factory, job_id)
    assert job.status == "failed" and job.attempts == 1 and "nope" in job.error


def test_register_rejects_duplicate_kind():
    runner.register("k")(lambda s, j: None)
    with pytest.raises(ValueError):
        runner.register("k")(lambda s, j: None)


def test_run_forever_stops_on_event(factory):
    stop = threading.Event()
    done = []

    @runner.register("test.ok")
    def handle(session, job):
        done.append(job.id)
        if len(done) == 2:
            stop.set()

    _enqueue(factory)
    _enqueue(factory)
    t = threading.Thread(target=runner.run_forever, args=(factory,), kwargs={"poll_interval": 0.01,
                                                                             "stop_event": stop})
    t.start()
    t.join(timeout=10)
    assert not t.is_alive() and len(done) == 2
