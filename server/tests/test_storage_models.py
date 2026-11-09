import os

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from matrix_server.storage.annotations import complete_annotation, open_annotation
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory
from matrix_server.storage.models import (
    Annotation,
    Dataset,
    DatasetItem,
    Experiment,
    ExperimentResult,
    PromptLabel,
    PromptVersion,
)

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def session(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'matrix.db'}")
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    init_db(eng)
    s = make_session_factory(eng)()
    yield s
    s.close()
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


def _prompt(project_id: str, version: int = 1) -> PromptVersion:
    return PromptVersion(project_id=project_id, name="qa", version=version, type="text", template="Hi {{x}}")


def test_prompt_version_unique_per_project(session):
    session.add_all([_prompt("p1"), _prompt("p1", 2), _prompt("p2")])
    session.commit()
    session.add(_prompt("p1"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_prompt_template_roundtrips_json(session):
    messages = [{"role": "system", "content": "be nice"}, {"role": "user", "content": "{{q}}"}]
    pv = PromptVersion(
        project_id="p1", name="chat", version=1, type="chat", template=messages, variables=["q"],
        config={"model": "gpt", "temperature": 0.2}, response_schema={"type": "object"},
    )
    session.add(pv)
    session.commit()
    session.expire_all()
    loaded = session.get(PromptVersion, pv.id)
    assert loaded.template == messages
    assert loaded.variables == ["q"]
    assert loaded.config["temperature"] == 0.2


def test_prompt_label_unique_per_project(session):
    session.add_all([
        PromptLabel(project_id="p1", name="qa", label="production", version=1),
        PromptLabel(project_id="p2", name="qa", label="production", version=3),
    ])
    session.commit()
    session.add(PromptLabel(project_id="p1", name="qa", label="production", version=2))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_dataset_name_unique_per_project(session):
    session.add_all([Dataset(project_id="p1", name="golden"), Dataset(project_id="p2", name="golden")])
    session.commit()
    session.add(Dataset(project_id="p1", name="golden"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_dataset_item_metadata_column_and_cascade(session):
    ds = Dataset(project_id="p1", name="golden")
    session.add(ds)
    session.flush()
    item = DatasetItem(
        dataset_id=ds.id, project_id="p1", input={"q": "2+2"}, expected_output="4", context=["math"],
        metadata_={"tag": "smoke"}, source_trace_id="t" * 32,
    )
    session.add(item)
    session.commit()
    row = session.execute(select(DatasetItem.__table__.c.metadata)).scalar_one()
    assert row == {"tag": "smoke"}
    # database-level cascade (bulk delete bypasses the ORM relationship)
    session.execute(delete(Dataset).where(Dataset.id == ds.id))
    session.commit()
    assert session.scalars(select(DatasetItem)).all() == []


def test_experiment_results_cascade_and_unique(session):
    ds = Dataset(project_id="p1", name="golden")
    session.add(ds)
    session.flush()
    exp = Experiment(project_id="p1", name="e1", dataset_id=ds.id, variants=[{"label": "a"}], metrics=[])
    session.add(exp)
    session.flush()
    session.add(ExperimentResult(experiment_id=exp.id, project_id="p1", item_id="i1", variant="a", scores={}))
    session.commit()
    assert exp.status == "queued"
    session.add(ExperimentResult(experiment_id=exp.id, project_id="p1", item_id="i1", variant="a"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.execute(delete(Experiment).where(Experiment.id == exp.id))
    session.commit()
    assert session.scalars(select(ExperimentResult)).all() == []


def test_dataset_with_experiments_cannot_be_deleted(session):
    ds = Dataset(project_id="p1", name="golden")
    session.add(ds)
    session.flush()
    session.add(Experiment(project_id="p1", name="e1", dataset_id=ds.id))
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(delete(Dataset).where(Dataset.id == ds.id))
        session.commit()
    session.rollback()


def test_open_annotation_dedups_pending(session):
    first = open_annotation(session, "p1", "trace-1", "low score")
    session.commit()
    again = open_annotation(session, "p1", "trace-1", "negative feedback")
    assert again.id == first.id
    other_project = open_annotation(session, "p2", "trace-1", "low score")
    assert other_project.id != first.id
    session.commit()
    assert len(session.scalars(select(Annotation)).all()) == 2

    complete_annotation(session, first)
    session.commit()
    assert first.status == "done" and first.dedup_key is None and first.completed_at is not None
    reopened = open_annotation(session, "p1", "trace-1", "again")
    session.commit()
    assert reopened.id != first.id and reopened.status == "pending"


def test_second_pending_annotation_violates_constraint(session):
    open_annotation(session, "p1", "trace-1", "x")
    session.commit()
    session.add(Annotation(project_id="p1", trace_id="trace-1", status="pending", dedup_key="trace-1"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_open_annotation_recovers_from_concurrent_insert(session, monkeypatch):
    """Simulate losing the SELECT-then-INSERT race: the insert conflicts, the winner is returned."""
    winner = Annotation(project_id="p1", trace_id="trace-9", status="pending", dedup_key="trace-9")
    session.add(winner)
    session.commit()

    from matrix_server.storage import annotations

    real = annotations._pending
    calls = {"n": 0}

    def flaky(s, project_id, trace_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(s, project_id, trace_id)

    monkeypatch.setattr(annotations, "_pending", flaky)
    got = open_annotation(session, "p1", "trace-9", "x")
    assert got.id == winner.id
    session.commit()
