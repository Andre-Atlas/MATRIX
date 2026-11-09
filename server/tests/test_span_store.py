import os
import uuid
from datetime import timedelta

import pytest
from otlp_payloads import AGENT_ID, AGENT_TRACE_ID, ERROR_TRACE_ID, GENAI_TRACE_ID, LLM1_ID, T0, full_request

from matrix_server.config import Settings
from matrix_server.domain.spans import Span, SpanKind
from matrix_server.ingest.openinference import normalize
from matrix_server.ingest.otlp import decode_request
from matrix_server.storage.spans import (
    ClickHouseSpanStore,
    InvalidCursor,
    MemorySpanStore,
    SpanStore,
    build_span_store,
    percentile,
)

CLICKHOUSE_URL = os.environ.get("MATRIX_TEST_CLICKHOUSE_URL")


@pytest.fixture(scope="session")
def clickhouse_store():
    if not CLICKHOUSE_URL:
        pytest.skip("MATRIX_TEST_CLICKHOUSE_URL not set")
    store = ClickHouseSpanStore(CLICKHOUSE_URL, f"matrix_test_{uuid.uuid4().hex[:12]}")
    store.ensure_schema()
    store.ensure_schema()  # idempotent
    yield store
    store.drop_database()


@pytest.fixture(params=["memory", "clickhouse"])
def store(request):
    if request.param == "memory":
        return MemorySpanStore()
    return request.getfixturevalue("clickhouse_store")


@pytest.fixture
def project():
    # Unique per test so tests sharing the ClickHouse database stay independent.
    return f"proj-{uuid.uuid4().hex[:8]}"


def ingest_fixture(store, project_id):
    raw = decode_request(full_request().SerializeToString(), "application/x-protobuf")
    spans = [normalize(r, project_id, 100_000) for r in raw]
    store.insert(spans)
    return spans


def mk(project_id, trace_n, span_n=0, *, parent=None, start_s=0.0, dur_ms=100.0, **kw):
    start = T0 + timedelta(seconds=start_s)
    return Span(
        project_id=project_id,
        trace_id=f"{trace_n:032x}",
        span_id=f"{span_n + 1:016x}",
        parent_span_id=parent,
        name=kw.pop("name", f"trace-{trace_n}"),
        kind=kw.pop("kind", SpanKind.CHAIN),
        start_time=start,
        end_time=start + timedelta(milliseconds=dur_ms),
        duration_ms=dur_ms,
        **kw,
    )


def test_protocol_conformance(store):
    assert isinstance(store, SpanStore)


def test_roundtrip_and_trace_summary(store, project):
    spans = ingest_fixture(store, project)
    got = store.get_trace(project, AGENT_TRACE_ID)
    assert got is not None
    summary, stored = got
    expected = sorted(
        (s for s in spans if s.trace_id == AGENT_TRACE_ID), key=lambda s: (s.start_time, s.span_id)
    )
    assert stored == expected
    assert summary.name == "support-agent"
    assert summary.root_kind is SpanKind.AGENT
    assert summary.span_count == 5
    assert summary.status == "ok"
    assert summary.session_id == "sess-1" and summary.user_id == "user-1"
    assert summary.start_time == T0
    assert summary.duration_ms == 5000
    assert summary.input_tokens == 420 and summary.output_tokens == 70
    assert summary.total_cost_usd == pytest.approx(sum(s.cost_usd for s in expected))
    assert summary.input_preview == "Where is my order 42?"
    assert summary.output_preview == "Your order ships tomorrow."
    assert summary.tags == ["prod", "support"]
    assert summary.scores == {}
    span = store.get_span(project, AGENT_TRACE_ID, LLM1_ID)
    assert span is not None and span.model == "gpt-4o" and span.ttft_ms == 250
    assert store.get_span(project, AGENT_TRACE_ID, "ffffffffffffffff") is None
    assert store.get_trace(project, "f" * 32) is None


def test_list_traces_matches_get_trace(store, project):
    ingest_fixture(store, project)
    items, cursor = store.list_traces(project)
    assert cursor is None
    assert {t.trace_id for t in items} == {AGENT_TRACE_ID, GENAI_TRACE_ID, ERROR_TRACE_ID}
    for item in items:
        assert item == store.get_trace(project, item.trace_id)[0]
    error = next(t for t in items if t.trace_id == ERROR_TRACE_ID)
    assert error.status == "error" and error.root_kind is SpanKind.CHAIN


def test_pagination_descending_and_stable(store, project):
    # 7 traces; two share the same start time to exercise the trace_id tie-break.
    starts = [0, 10, 20, 20, 30, 40, 50]
    store.insert([mk(project, n + 1, start_s=s) for n, s in enumerate(starts)])
    seen, cursor, pages = [], None, 0
    while True:
        items, cursor = store.list_traces(project, limit=3, cursor=cursor)
        seen.extend(items)
        pages += 1
        if cursor is None:
            break
    assert pages == 3
    assert [len(p) for p in (seen[:3], seen[3:6], seen[6:])] == [3, 3, 1]
    keys = [(t.start_time, t.trace_id) for t in seen]
    assert keys == sorted(keys, reverse=True)
    assert len({t.trace_id for t in seen}) == 7


def test_invalid_cursor(store, project):
    with pytest.raises(InvalidCursor):
        store.list_traces(project, cursor="!!not-a-cursor")
    with pytest.raises(InvalidCursor):
        store.list_sessions(project, cursor="e30")  # base64 of "{}"


def test_filters(store, project):
    store.insert(
        [
            mk(project, 1, start_s=0, session_id="s1", user_id="u1", name="alpha"),
            mk(project, 1, 1, parent=f"{1:016x}", start_s=0.01, status="error", kind=SpanKind.LLM),
            mk(project, 2, start_s=60, session_id="s1", user_id="u2", name="beta", kind=SpanKind.AGENT),
            mk(project, 3, start_s=120, session_id="s2", user_id="u2", name="alpha", tags=["x"]),
        ]
    )

    def ids(**kw):
        return sorted(int(t.trace_id, 16) for t in store.list_traces(project, **kw)[0])

    assert ids() == [1, 2, 3]
    assert ids(session_id="s1") == [1, 2]
    assert ids(user_id="u2") == [2, 3]
    assert ids(name="alpha") == [1, 3]
    assert ids(status="error") == [1]
    assert ids(status="ok") == [2, 3]
    assert ids(status="bogus") == []
    assert ids(kind="AGENT") == [2]
    assert ids(kind="CHAIN") == [1, 3]
    assert ids(since=T0 + timedelta(seconds=30)) == [2, 3]
    assert ids(until=T0 + timedelta(seconds=60)) == [1, 2]
    assert ids(since=T0 + timedelta(seconds=30), until=T0 + timedelta(seconds=90)) == [2]
    assert ids(session_id="s1", user_id="u1") == [1]
    assert ids(session_id="nope") == []


def test_since_excludes_trace_started_before(store, project):
    # The trace started before `since` but has a span after it: it must not be listed.
    store.insert(
        [
            mk(project, 1, start_s=0, dur_ms=120_000),
            mk(project, 1, 1, parent=f"{1:016x}", start_s=100),
        ]
    )
    assert store.list_traces(project, since=T0 + timedelta(seconds=50))[0] == []
    assert store.overview(project, since=T0 + timedelta(seconds=50))["trace_count"] == 0


def test_root_fallback_to_earliest_span(store, project):
    # No parentless span (root not exported yet): the earliest span acts as root.
    store.insert(
        [
            mk(project, 1, 1, parent="ab" * 8, start_s=2, name="late"),
            mk(project, 1, 2, parent="ab" * 8, start_s=1, name="early", input="in", kind=SpanKind.TOOL),
        ]
    )
    (item,), _ = store.list_traces(project)
    assert (item.name, item.root_kind, item.input_preview) == ("early", SpanKind.TOOL, "in")
    assert store.get_trace(project, item.trace_id)[0] == item


def test_preview_truncated_to_300(store, project):
    store.insert([mk(project, 1, input="é" * 400, output="o" * 10)])
    (item,), _ = store.list_traces(project)
    assert item.input_preview == "é" * 300
    assert item.output_preview == "o" * 10


def test_sessions(store, project):
    store.insert(
        [
            mk(project, 1, start_s=0, session_id="s1", cost_usd=0.5),
            mk(project, 1, 1, parent=f"{1:016x}", start_s=0.01, cost_usd=0.25),  # no session attr
            mk(project, 2, start_s=60, session_id="s1", user_id="u1", cost_usd=1.0),
            mk(project, 3, start_s=120, session_id="s2", user_id="u2"),
            mk(project, 4, start_s=180, session_id="s3"),
            mk(project, 5, start_s=240),  # no session
        ]
    )
    items, cursor = store.list_sessions(project, limit=2)
    assert [s.session_id for s in items] == ["s3", "s2"]
    rest, cursor2 = store.list_sessions(project, limit=2, cursor=cursor)
    assert [s.session_id for s in rest] == ["s1"] and cursor2 is None
    s1 = rest[0]
    assert s1.trace_count == 2
    assert s1.user_id == "u1"
    assert s1.first_seen == T0
    assert s1.last_seen == T0 + timedelta(seconds=60, milliseconds=100)
    assert s1.total_cost_usd == pytest.approx(1.75)

    got = store.get_session(project, "s1")
    assert got is not None
    summary, traces, next_cursor = got
    assert summary == s1
    assert [int(t.trace_id, 16) for t in traces] == [2, 1]
    assert next_cursor is None
    assert store.get_session(project, "missing") is None


def test_get_session_paginates_traces(store, project):
    store.insert([mk(project, n, start_s=n, session_id="s1") for n in range(1, 6)])
    summary, page1, cursor = store.get_session(project, "s1", limit=2)
    assert summary.trace_count == 5
    assert [int(t.trace_id, 16) for t in page1] == [5, 4]
    _, page2, cursor = store.get_session(project, "s1", limit=2, cursor=cursor)
    _, page3, cursor = store.get_session(project, "s1", limit=2, cursor=cursor)
    assert [int(t.trace_id, 16) for t in page2 + page3] == [3, 2, 1]
    assert cursor is None


def test_overview(store, project):
    store.insert(
        [
            mk(project, 1, start_s=0, dur_ms=100),
            mk(
                project,
                1,
                1,
                parent=f"{1:016x}",
                start_s=0.01,
                dur_ms=50,
                kind=SpanKind.LLM,
                model="gpt-4o",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.5,
            ),
            mk(
                project,
                2,
                start_s=60,
                dur_ms=300,
                status="error",
                kind=SpanKind.LLM,
                model="gpt-4o",
                cost_usd=0.25,
                input_tokens=1,
            ),
            mk(
                project,
                3,
                start_s=120,
                dur_ms=1000,
                kind=SpanKind.LLM,
                model="claude-haiku-4-5",
                cost_usd=2.0,
                output_tokens=7,
            ),
            # aggregated usage repeated on an AGENT span is not counted twice
            mk(project, 4, 1, parent=f"{4:016x}", start_s=180, kind=SpanKind.AGENT, input_tokens=99),
            mk(project, 4, start_s=180, dur_ms=200),
        ]
    )
    ov = store.overview(project)
    assert ov["trace_count"] == 4
    assert ov["error_rate"] == pytest.approx(0.25)
    durations = [100, 300, 1000, 200]
    assert ov["p50_latency_ms"] == pytest.approx(percentile(durations, 0.5))
    assert ov["p95_latency_ms"] == pytest.approx(percentile(durations, 0.95))
    assert ov["total_cost_usd"] == pytest.approx(2.75)
    assert (ov["input_tokens"], ov["output_tokens"]) == (11, 12)
    assert [(m["model"], m["calls"]) for m in ov["by_model"]] == [("claude-haiku-4-5", 1), ("gpt-4o", 2)]
    assert ov["by_model"][1]["cost_usd"] == pytest.approx(0.75)

    recent = store.overview(project, since=T0 + timedelta(seconds=90))
    assert recent["trace_count"] == 2
    assert [m["model"] for m in recent["by_model"]] == ["claude-haiku-4-5"]

    empty = store.overview(f"{project}-empty")
    assert empty == {
        "trace_count": 0,
        "error_rate": 0.0,
        "p50_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "total_cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "by_model": [],
    }


def test_project_isolation(store, project):
    other = f"{project}-b"
    ingest_fixture(store, project)
    assert store.get_trace(other, AGENT_TRACE_ID) is None
    assert store.get_span(other, AGENT_TRACE_ID, AGENT_ID) is None
    assert store.list_traces(other) == ([], None)
    assert store.list_traces(other, session_id="sess-1") == ([], None)
    assert store.list_sessions(other) == ([], None)
    assert store.get_session(other, "sess-1") is None
    assert store.overview(other)["trace_count"] == 0
    # the same trace id in another project stays separate
    store.insert([mk(other, int(AGENT_TRACE_ID, 16), name="b-only")])
    assert store.get_trace(other, AGENT_TRACE_ID)[0].name == "b-only"
    assert store.get_trace(project, AGENT_TRACE_ID)[0].name == "support-agent"
    assert store.get_trace(project, AGENT_TRACE_ID)[0].span_count == 5


def test_insert_requires_project(store):
    span = mk("x", 1)
    span.project_id = ""
    with pytest.raises(ValueError):
        store.insert([span])


def test_percentile():
    assert percentile([], 0.5) == 0.0
    assert percentile([5], 0.95) == 5
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([10, 20], 0.95) == pytest.approx(19.5)


def test_build_span_store_memory():
    assert isinstance(build_span_store(Settings(span_store="memory")), MemorySpanStore)


def test_build_span_store_clickhouse(clickhouse_store):
    settings = Settings(
        span_store="clickhouse", clickhouse_url=CLICKHOUSE_URL, clickhouse_database=clickhouse_store.database
    )
    built = build_span_store(settings)
    assert isinstance(built, ClickHouseSpanStore)


def test_clickhouse_rejects_bad_database_name():
    with pytest.raises(ValueError):
        ClickHouseSpanStore("http://localhost:8123", "bad`name", client=object())


def test_exporter_retry_is_counted_once(store, project):
    # The same spans inserted twice (exporter retry) must not inflate any aggregate.
    spans = [
        mk(project, 1, start_s=0, session_id="s1", kind=SpanKind.AGENT),
        mk(
            project,
            1,
            1,
            parent=f"{1:016x}",
            start_s=0.01,
            kind=SpanKind.LLM,
            model="gpt-4o",
            input_tokens=100,
            output_tokens=10,
            cost_usd=0.5,
        ),
    ]
    store.insert(spans)
    before = (
        store.list_traces(project)[0],
        store.get_trace(project, f"{1:032x}")[0],
        store.list_sessions(project)[0],
        store.get_session(project, "s1"),
        store.overview(project),
    )
    store.insert(spans)
    store.insert(spans[1:])
    after = (
        store.list_traces(project)[0],
        store.get_trace(project, f"{1:032x}")[0],
        store.list_sessions(project)[0],
        store.get_session(project, "s1"),
        store.overview(project),
    )
    assert after == before
    (summary,) = after[0]
    assert (summary.span_count, summary.input_tokens, summary.total_cost_usd) == (2, 100, 0.5)
    assert after[4]["by_model"] == [{"model": "gpt-4o", "calls": 1, "cost_usd": 0.5}]
    assert after[2][0].total_cost_usd == pytest.approx(0.5)


def test_retry_last_write_wins(store, project):
    first = mk(project, 1, kind=SpanKind.LLM, model="gpt-4o", cost_usd=0.5, input_tokens=1)
    second = first.model_copy(update={"cost_usd": 0.75, "input_tokens": 2})
    store.insert([first])
    store.insert([second])
    (summary,), _ = store.list_traces(project)
    assert (summary.span_count, summary.total_cost_usd, summary.input_tokens) == (1, 0.75, 2)
    assert store.overview(project)["by_model"][0]["cost_usd"] == 0.75
    assert store.get_span(project, first.trace_id, first.span_id).cost_usd == 0.75
    # in-batch duplicates: the later one wins too
    store.insert([second.model_copy(update={"cost_usd": 1.0}), second.model_copy(update={"cost_usd": 2.0})])
    assert store.list_traces(project)[0][0].total_cost_usd == 2.0


def test_since_uses_trace_start_not_late_children(store, project):
    # Root 48h before `since`, child 1h after: the trace started before `since`, so it is excluded.
    since = T0 + timedelta(days=3)
    store.insert(
        [
            mk(project, 1, start_s=(since - T0).total_seconds() - 48 * 3600, dur_ms=3 * 3600 * 1000),
            mk(project, 1, 1, parent=f"{1:016x}", start_s=(since - T0).total_seconds() + 3600, kind=SpanKind.LLM),
            mk(project, 2, start_s=(since - T0).total_seconds() + 60),
        ]
    )
    items, _ = store.list_traces(project, since=since)
    assert [int(t.trace_id, 16) for t in items] == [2]
    assert store.overview(project, since=since)["trace_count"] == 1
    (old,), _ = store.list_traces(project, until=since - timedelta(hours=1))
    assert old.span_count == 2 and old.start_time == since - timedelta(hours=48)


def test_token_totals_only_from_llm_and_embedding(store, project):
    store.insert(
        [
            mk(project, 1, kind=SpanKind.AGENT, input_tokens=150, output_tokens=15),
            mk(project, 1, 1, parent=f"{1:016x}", kind=SpanKind.LLM, input_tokens=100, output_tokens=10),
            mk(project, 1, 2, parent=f"{1:016x}", kind=SpanKind.EMBEDDING, input_tokens=50),
            mk(project, 1, 3, parent=f"{1:016x}", kind=SpanKind.CHAIN, input_tokens=150, output_tokens=10),
        ]
    )
    (summary,), _ = store.list_traces(project)
    assert (summary.input_tokens, summary.output_tokens) == (150, 10)
    assert store.get_trace(project, summary.trace_id)[0].input_tokens == 150
    ov = store.overview(project)
    assert (ov["input_tokens"], ov["output_tokens"]) == (150, 10)


def test_max_limit_is_500(store, project):
    from matrix_server.storage.spans import MAX_LIMIT

    assert MAX_LIMIT == 500
    store.insert([mk(project, n, start_s=n) for n in range(1, 503)])
    items, cursor = store.list_traces(project, limit=10_000)
    assert len(items) == 500 and cursor is not None


def test_clickhouse_schema_is_replacing_merge_tree(clickhouse_store):
    rows = clickhouse_store._client.query(
        "SELECT engine_full FROM system.tables WHERE database = {db:String} AND name = 'spans'",
        parameters={"db": clickhouse_store.database},
    ).result_rows
    assert rows[0][0].startswith("ReplacingMergeTree(version)")


def test_clickhouse_retry_deduped_after_merge(clickhouse_store, project):
    span = mk(project, 1, kind=SpanKind.LLM, model="gpt-4o", cost_usd=0.5)
    clickhouse_store.insert([span])
    clickhouse_store.insert([span.model_copy(update={"cost_usd": 0.75})])
    clickhouse_store._client.command(f"OPTIMIZE TABLE {clickhouse_store.table} FINAL")
    count = clickhouse_store._client.query(
        f"SELECT count() FROM {clickhouse_store.table} WHERE project_id = {{p:String}}",
        parameters={"p": project},
    ).result_rows[0][0]
    assert count == 1
    assert clickhouse_store.list_traces(project)[0][0].total_cost_usd == 0.75
