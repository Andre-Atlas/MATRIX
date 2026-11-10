"""Metric stores (ADR-008): memory and ClickHouse agree point for point, M4 keeps extrema."""

import math
import os
import random
import time
import uuid

import pytest

from matrix_server.config import Settings
from matrix_server.runs.store import (
    ClickHouseMetricStore,
    MemoryMetricStore,
    MetricPoint,
    MetricStore,
    build_metric_store,
)

CLICKHOUSE_URL = os.environ.get("MATRIX_TEST_CLICKHOUSE_URL")
T0 = 1_750_000_000_000_000  # µs


@pytest.fixture(scope="session")
def clickhouse_metric_store():
    if not CLICKHOUSE_URL:
        pytest.skip("MATRIX_TEST_CLICKHOUSE_URL not set")
    store = ClickHouseMetricStore(CLICKHOUSE_URL, f"matrix_test_runs_{uuid.uuid4().hex[:12]}")
    store.ensure_schema()
    store.ensure_schema()  # idempotent
    yield store
    store.drop_database()


@pytest.fixture(params=["memory", "clickhouse"])
def store(request):
    if request.param == "memory":
        return MemoryMetricStore()
    return request.getfixturevalue("clickhouse_metric_store")


@pytest.fixture
def both(request):
    """(memory, clickhouse) pair for comparisons; skipped without ClickHouse."""
    return MemoryMetricStore(), request.getfixturevalue("clickhouse_metric_store")


@pytest.fixture
def project():
    return f"proj-{uuid.uuid4().hex[:8]}"


def pts(key, steps, values, ts=None):
    ts = ts or [T0 + s * 1000 for s in steps]
    return [MetricPoint(key, int(s), float(v), int(t)) for s, v, t in zip(steps, values, ts, strict=True)]


def insert_both(stores, project_id, run_id, points, chunk=50_000):
    for s in stores:
        for i in range(0, len(points), chunk):
            s.insert(project_id, run_id, points[i : i + chunk])


def test_protocol_conformance(store):
    assert isinstance(store, MetricStore)


def test_build_metric_store_memory():
    assert isinstance(build_metric_store(Settings(span_store="memory")), MemoryMetricStore)


def test_small_series_returned_whole_and_ordered(store, project):
    steps = [5, 1, 3, 2, 4]
    store.insert(project, "r", pts("loss", steps, [0.5, 0.1, 0.3, 0.2, 0.4]))
    series, down = store.read_series(project, ["r"], ["loss", "missing"])
    assert down is False
    assert set(series["r"]) == {"loss"}
    s = series["r"]["loss"]
    assert s.steps == [1, 2, 3, 4, 5]
    assert s.values == [0.1, 0.2, 0.3, 0.4, 0.5]
    assert s.ts_us == [T0 + i * 1000 for i in [1, 2, 3, 4, 5]]


def test_resend_is_idempotent_last_write_wins(store, project):
    store.insert(project, "r", pts("loss", range(10), [1.0] * 10))
    store.insert(project, "r", pts("loss", range(10), [1.0] * 10))  # SDK retry
    store.insert(project, "r", pts("loss", [3], [9.0]))
    # In-batch duplicate: the later point wins.
    store.insert(project, "r", [MetricPoint("loss", 4, 7.0, T0), MetricPoint("loss", 4, 8.0, T0)])
    s = store.read_series(project, ["r"], ["loss"])[0]["r"]["loss"]
    assert s.steps == list(range(10))
    assert s.values[3] == 9.0 and s.values[4] == 8.0
    stats = {k.key: k for k in store.key_stats(project, "r")}
    assert stats["loss"].count == 10
    assert stats["loss"].max == 9.0


def test_non_finite_values_read_as_null(store, project):
    store.insert(project, "r", pts("x", [0, 1, 2, 3], [math.nan, math.inf, -math.inf, 2.5]))
    s = store.read_series(project, ["r"], ["x"])[0]["r"]["x"]
    assert s.values == [None, None, None, 2.5]
    (stats,) = store.key_stats(project, "r")
    assert (stats.min, stats.max, stats.last_value) == (2.5, 2.5, 2.5)
    store.insert(project, "r", pts("y", [0], [math.nan]))
    y = {k.key: k for k in store.key_stats(project, "r")}["y"]
    assert (y.min, y.max, y.last_value, y.count) == (None, None, None, 1)
    assert store.summaries(project, ["r"])["r"] == {"x": 2.5, "y": None}


def test_summaries_last_step_non_sys_capped_and_batched(store, project):
    store.insert(project, "a", pts("loss", [1, 9, 5], [3.0, 1.0, 2.0]))
    store.insert(project, "a", pts("sys/cpu.percent", [1], [50.0]))
    store.insert(project, "b", pts("acc", [0], [0.5]))
    store.insert(project, "c", [MetricPoint(f"k{i:03d}", 0, float(i), T0) for i in range(205)])
    out = store.summaries(project, ["a", "b", "c", "nope"], max_keys=200)
    assert out["a"] == {"loss": 1.0}
    assert out["b"] == {"acc": 0.5}
    assert len(out["c"]) == 200 and "k000" in out["c"] and "k204" not in out["c"]
    assert "nope" not in out


def test_project_isolation(store, project):
    other = project + "-other"
    store.insert(project, "r", pts("loss", [0], [1.0]))
    store.insert(other, "r", pts("loss", [0], [2.0]))
    assert store.read_series(other, ["r"], ["loss"])[0]["r"]["loss"].values == [2.0]
    assert store.summaries(other, ["r"]) == {"r": {"loss": 2.0}}
    assert store.read_series(project + "-none", ["r"], ["loss"])[0] == {}
    assert store.keys(project + "-none", "r") == []
    store.delete_run(other, "r")


def test_delete_run(store, project):
    store.insert(project, "r", pts("loss", range(5), range(5)))
    store.insert(project, "keep", pts("loss", range(5), range(5)))
    if isinstance(store, ClickHouseMetricStore):
        store.delete_run(project, "r", wait=True)
    else:
        store.delete_run(project, "r")
    assert store.read_series(project, ["r"], ["loss"])[0] == {}
    assert store.key_stats(project, "r") == []
    assert store.keys(project, "keep") == ["loss"]


def test_step_range_filter(store, project):
    store.insert(project, "r", pts("loss", range(100), range(100)))
    s = store.read_series(project, ["r"], ["loss"], from_step=10, to_step=19)[0]["r"]["loss"]
    assert s.steps == list(range(10, 20))


def _check_m4(series, max_points, raw):
    """Shape and correctness against the raw (deduplicated) data."""
    assert len(series.steps) <= max_points
    assert series.steps == sorted(series.steps) and len(set(series.steps)) == len(series.steps)
    by_step = dict(raw)
    for step, value in zip(series.steps, series.values, strict=True):
        expected = by_step[step]
        assert (value is None) if not math.isfinite(expected) else value == expected
    finite = [v for _, v in raw if math.isfinite(v)]
    got = [v for v in series.values if v is not None]
    assert min(got) == min(finite) and max(got) == max(finite)
    assert series.steps[0] == min(s for s, _ in raw) and series.steps[-1] == max(s for s, _ in raw)


def test_m4_stores_agree_on_random_data(both, project):
    rng = random.Random(7)
    steps = sorted(rng.sample(range(0, 300_000), 20_000))  # gaps and non-uniform spacing
    values = [rng.gauss(0, 1) for _ in steps]
    for i in rng.sample(range(len(values)), 50):
        values[i] = rng.choice([math.nan, math.inf, -math.inf])
    values[1234] = 42.0  # spike
    values[4321] = -42.0  # dip
    ties = [rng.choice([1.0, 2.0]) for _ in range(5000)]  # many equal values: tie rules matter
    insert_both(both, project, "r", pts("noise", steps, values))
    insert_both(both, project, "r", pts("ties", range(5000), ties))
    insert_both(both, project, "r", pts("short", [0, 10, 1000], [1, 2, 3]))
    insert_both(both, project, "r2", pts("noise", steps[:3000], values[:3000]))
    # Resend part of the data with new values: both must keep the last write.
    insert_both(both, project, "r", pts("noise", steps[:100], [v + 1 for v in values[:100]]))
    memory, clickhouse = both
    for max_points in (4, 100, 1000, 5000):
        for x in ("step", "time"):
            args = dict(max_points=max_points, x=x)
            got_m = memory.read_series(project, ["r", "r2"], ["noise", "ties", "short"], **args)
            got_c = clickhouse.read_series(project, ["r", "r2"], ["noise", "ties", "short"], **args)
            assert got_m == got_c, (max_points, x)
            assert got_m[1] is True
    assert memory.key_stats(project, "r") == clickhouse.key_stats(project, "r")
    assert memory.summaries(project, ["r", "r2"]) == clickhouse.summaries(project, ["r", "r2"])
    assert memory.keys(project, "r") == clickhouse.keys(project, "r") == ["noise", "short", "ties"]
    raw = list(zip(steps, [v + 1 for v in values[:100]] + values[100:], strict=True))
    series = memory.read_series(project, ["r"], ["noise"], max_points=1000)[0]["r"]["noise"]
    _check_m4(series, 1000, raw)
    assert 42.0 in series.values and -42.0 in series.values


def test_m4_time_axis_orders_by_ts(store, project):
    # Steps restart (e.g. two phases) while time keeps increasing.
    steps = list(range(3000)) + list(range(3000, 6000))
    ts = [T0 + i * 10 for i in range(3000, 6000)] + [T0 + i * 10 for i in range(3000)]
    store.insert(project, "r", pts("lr", steps, [float(i % 7) for i in range(6000)], ts))
    s, down = store.read_series(project, ["r"], ["lr"], max_points=400, x="time")
    assert down is True
    series = s["r"]["lr"]
    assert series.ts_us == sorted(series.ts_us)
    assert len(series.steps) <= 400
    assert series.ts_us[0] == T0 and series.ts_us[-1] == T0 + 5999 * 10


def test_m4_spike_survives_in_two_million_points(store, project):
    n = 2_000_000
    spike_at = 1_234_567
    values = [math.sin(i / 5000.0) for i in range(n)]
    values[spike_at] = 1e6
    chunk = 200_000
    for i in range(0, n, chunk):
        store.insert(project, "big", [MetricPoint("loss", s, values[s], T0 + s) for s in range(i, min(n, i + chunk))])
    series, down = store.read_series(project, ["big"], ["loss"], max_points=1000)
    s = series["big"]["loss"]
    assert down is True
    assert len(s.steps) <= 1000
    assert s.steps == sorted(s.steps)
    assert 1e6 in s.values and s.steps[s.values.index(1e6)] == spike_at
    assert s.steps[0] == 0 and s.steps[-1] == n - 1
    store.delete_run(project, "big")


def test_one_million_points_read_under_one_second_on_clickhouse(clickhouse_metric_store, project):
    store = clickhouse_metric_store
    n = 1_000_000
    started = time.perf_counter()
    for i in range(0, n, 100_000):
        store.insert(
            project,
            "perf",
            [MetricPoint("loss", s, 1.0 / (s + 1), T0 + s * 1000) for s in range(i, i + 100_000)],
        )
    ingest = time.perf_counter() - started
    store.read_series(project, ["perf"], ["loss"], max_points=1000)  # warm-up
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        series, down = store.read_series(project, ["perf"], ["loss"], max_points=1000)
        timings.append(time.perf_counter() - started)
    assert down is True and len(series["perf"]["loss"].steps) <= 1000
    print(f"\n1M points: ingest {ingest:.2f}s, M4 read best {min(timings) * 1000:.0f} ms")
    assert min(timings) < 1.0
    store.delete_run(project, "perf")
