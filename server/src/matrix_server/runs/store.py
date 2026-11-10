"""Metric point storage for training runs (ADR-008): the `MetricStore` protocol, an in-memory store
and a ClickHouse store with identical semantics.

A point is identified by (project_id, run_id, key, step); writing it again replaces it (the last
write wins), which makes SDK retries idempotent. Values are floats and may be NaN/±Inf; reads
return non-finite values as `None`. Times are microseconds since the epoch (UTC).

Series reads are downsampled with M4: a series with at most `max_points` points is returned whole;
a longer one is cut into `max_points // 4` buckets over its axis range (step, or time for
`x="time"`), bucket = `(a - lo) * buckets // (hi - lo + 1)`, and each bucket contributes its first
and last point (by axis, then step) and its minimum and maximum (finite values only; ties go to the
smaller step). The union is returned ordered by (axis, step), so a series never has more than
`max_points` points and every peak and valley of the line chart survives. Both stores produce the
same bucket rows and share `_assemble`, so their output is identical on the same data.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

from matrix_server.config import Settings

Axis = Literal["step", "time"]
SYS_PREFIX = "sys/"
DEFAULT_MAX_POINTS = 1000
MAX_MAX_POINTS = 5000
MIN_MAX_POINTS = 4
MAX_SUMMARY_KEYS = 200


@dataclass(frozen=True, slots=True)
class MetricPoint:
    key: str
    step: int
    value: float
    ts_us: int


@dataclass(slots=True)
class Series:
    steps: list[int] = field(default_factory=list)
    values: list[float | None] = field(default_factory=list)
    ts_us: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class KeyStats:
    key: str
    count: int
    first_step: int
    last_step: int
    last_value: float | None
    min: float | None
    max: float | None


# (run_id, key) -> {run_id: {key: Series}}
SeriesMap = dict[str, dict[str, Series]]


@runtime_checkable
class MetricStore(Protocol):
    def insert(self, project_id: str, run_id: str, points: Sequence[MetricPoint]) -> None: ...

    def keys(self, project_id: str, run_id: str, limit: int = 1000) -> list[str]:
        """Distinct keys of a run, sorted, at most `limit`."""
        ...

    def key_stats(self, project_id: str, run_id: str) -> list[KeyStats]: ...

    def read_series(
        self,
        project_id: str,
        run_ids: Sequence[str],
        keys: Sequence[str],
        *,
        max_points: int = DEFAULT_MAX_POINTS,
        x: Axis = "step",
        from_step: int | None = None,
        to_step: int | None = None,
    ) -> tuple[SeriesMap, bool]:
        """M4-downsampled series per run and key (missing ones are absent) and whether any series
        was downsampled."""
        ...

    def summaries(
        self, project_id: str, run_ids: Sequence[str], max_keys: int = MAX_SUMMARY_KEYS
    ) -> dict[str, dict[str, float | None]]:
        """Value at the last step of every non-`sys/` key (first `max_keys` keys by name) per run."""
        ...

    def delete_run(self, project_id: str, run_id: str) -> None: ...


# --------------------------------------------------------------------------- shared helpers


def clamp_max_points(max_points: int) -> int:
    return max(MIN_MAX_POINTS, min(int(max_points), MAX_MAX_POINTS))


def finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# One bucket: (n, first, last, min | None, max | None); a point is (step, value, ts_us).
_Point = tuple[int, float, int]
_Bucket = tuple[int, _Point, _Point, _Point | None, _Point | None]


def _assemble(buckets: Iterable[_Bucket], axis: Axis) -> tuple[Series, bool]:
    """Union of the bucket points, ordered by (axis, step); `True` if the series was downsampled."""
    points: dict[int, _Point] = {}
    downsampled = False
    for n, first, last, low, high in buckets:
        for point in (first, last, low, high):
            if point is not None:
                points[point[0]] = point
        downsampled = downsampled or n > 0
    if axis == "time":
        ordered = sorted(points.values(), key=lambda p: (p[2], p[0]))
    else:
        ordered = sorted(points.values(), key=lambda p: p[0])
    series = Series(
        steps=[p[0] for p in ordered],
        values=[finite_or_none(p[1]) for p in ordered],
        ts_us=[p[2] for p in ordered],
    )
    return series, downsampled


def _m4_buckets(points: list[_Point], max_points: int, axis: Axis) -> list[_Bucket]:
    """Python M4 over deduplicated points; same bucketing and tie rules as the ClickHouse query.
    The bucket's `n` is the series length when downsampled, else 0."""
    n = len(points)
    if n <= max_points:
        return [(0, p, p, None, None) for p in points]
    nb = max_points // 4
    ax = 0 if axis == "step" else 2
    lo = min(p[ax] for p in points)
    hi = max(p[ax] for p in points)
    width = hi - lo + 1
    groups: dict[int, list[_Point]] = {}
    for p in points:
        groups.setdefault((p[ax] - lo) * nb // width, []).append(p)
    out: list[_Bucket] = []
    for members in groups.values():
        first = min(members, key=lambda p: (p[ax], p[0]))
        last = max(members, key=lambda p: (p[ax], p[0]))
        finite = [p for p in members if math.isfinite(p[1])]
        low = min(finite, key=lambda p: (p[1], p[0])) if finite else None
        high = max(finite, key=lambda p: (p[1], -p[0])) if finite else None
        out.append((n, first, last, low, high))
    return out


# --------------------------------------------------------------------------- memory


class MemoryMetricStore:
    """Thread-safe in-memory store for tests and dev mode (same semantics as ClickHouse)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (project_id, run_id) -> key -> step -> (value, ts_us)
        self._data: dict[tuple[str, str], dict[str, dict[int, tuple[float, int]]]] = {}

    def insert(self, project_id: str, run_id: str, points: Sequence[MetricPoint]) -> None:
        if not project_id or not run_id:
            raise ValueError("points need a project_id and run_id")
        with self._lock:
            run = self._data.setdefault((project_id, run_id), {})
            for p in points:
                run.setdefault(p.key, {})[int(p.step)] = (float(p.value), int(p.ts_us))

    def _run(self, project_id: str, run_id: str) -> dict[str, dict[int, tuple[float, int]]]:
        return self._data.get((project_id, run_id), {})

    def keys(self, project_id: str, run_id: str, limit: int = 1000) -> list[str]:
        with self._lock:
            return sorted(k for k, steps in self._run(project_id, run_id).items() if steps)[: max(0, limit)]

    def key_stats(self, project_id: str, run_id: str) -> list[KeyStats]:
        out = []
        with self._lock:
            run = {k: dict(v) for k, v in self._run(project_id, run_id).items() if v}
        for key in sorted(run):
            steps = run[key]
            last = max(steps)
            finite = [v for v, _ in steps.values() if math.isfinite(v)]
            out.append(
                KeyStats(
                    key=key,
                    count=len(steps),
                    first_step=min(steps),
                    last_step=last,
                    last_value=finite_or_none(steps[last][0]),
                    min=min(finite) if finite else None,
                    max=max(finite) if finite else None,
                )
            )
        return out

    def read_series(
        self,
        project_id: str,
        run_ids: Sequence[str],
        keys: Sequence[str],
        *,
        max_points: int = DEFAULT_MAX_POINTS,
        x: Axis = "step",
        from_step: int | None = None,
        to_step: int | None = None,
    ) -> tuple[SeriesMap, bool]:
        max_points = clamp_max_points(max_points)
        result: SeriesMap = {}
        downsampled = False
        wanted = set(keys)
        for run_id in dict.fromkeys(run_ids):
            with self._lock:
                run = {k: list(v.items()) for k, v in self._run(project_id, run_id).items() if k in wanted}
            for key, items in run.items():
                points = [
                    (step, value, ts)
                    for step, (value, ts) in items
                    if (from_step is None or step >= from_step) and (to_step is None or step <= to_step)
                ]
                if not points:
                    continue
                series, down = _assemble(_m4_buckets(points, max_points, x), x)
                result.setdefault(run_id, {})[key] = series
                downsampled = downsampled or down
        return result, downsampled

    def summaries(
        self, project_id: str, run_ids: Sequence[str], max_keys: int = MAX_SUMMARY_KEYS
    ) -> dict[str, dict[str, float | None]]:
        out: dict[str, dict[str, float | None]] = {}
        with self._lock:
            for run_id in dict.fromkeys(run_ids):
                run = self._run(project_id, run_id)
                names = sorted(k for k, v in run.items() if v and not k.startswith(SYS_PREFIX))[:max_keys]
                if names:
                    out[run_id] = {k: finite_or_none(run[k][max(run[k])][0]) for k in names}
        return out

    def delete_run(self, project_id: str, run_id: str) -> None:
        with self._lock:
            self._data.pop((project_id, run_id), None)


# --------------------------------------------------------------------------- ClickHouse

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_COLUMNS = ("project_id", "run_id", "key", "step", "ts", "value", "version")


def _ch_point(value: Any) -> _Point:
    # `tuple(step, value, ts_us)` may come back as a named tuple (dict) depending on server settings.
    step, val, ts = value.values() if isinstance(value, dict) else value
    return int(step), float(val), int(ts)


class ClickHouseMetricStore:
    """Points in a ClickHouse `ReplacingMergeTree(version)` table (ADR-008), parameterized queries only.

    `ORDER BY (project_id, run_id, key, step)` makes one series a contiguous primary-key range.
    Each insert gets strictly increasing versions (server clock in ns, assigned here); merges keep
    the highest version and every read deduplicates first, so a re-sent point is never counted
    twice, merged or not: series and key stats read with `FINAL` (row with the highest version per
    (project, run, key, step); measured ~3x faster than `GROUP BY step` + `argMax(..., version)` or
    `LIMIT 1 BY` on 1M points), summaries take `argMax(value, (step, version))`. No partitioning by
    time: a re-sent point carries a new `ts` and must land in the same partition as the row it
    replaces (FINAL and merges only deduplicate within a partition).

    `read_series` is one query: dedup, per-series count and axis range with window functions,
    bucket assignment, then first/last/min/max per bucket; only the <= max_points bucket points per
    series leave ClickHouse. Bucket arithmetic is UInt64: the API bounds steps to < 2**53 and
    timestamps to 1970..2200, so `(a - lo) * buckets` cannot overflow. `delete_run` issues a lightweight `DELETE` that runs
    asynchronously (rows disappear from reads once the mutation applies).
    """

    def __init__(
        self,
        url: str,
        database: str = "matrix",
        *,
        async_insert: bool = True,
        client: Any = None,
    ) -> None:
        if not _IDENTIFIER.match(database):
            raise ValueError("invalid ClickHouse database name")
        self.database = database
        self.table = f"`{database}`.`run_metrics`"
        self._insert_settings = {"async_insert": 1, "wait_for_async_insert": 1} if async_insert else {}
        self._client = client if client is not None else self._connect(url)
        self._version_lock = threading.Lock()
        self._last_version = 0

    @staticmethod
    def _connect(url: str) -> Any:
        import clickhouse_connect

        parts = urlsplit(url)
        secure = parts.scheme == "https"
        return clickhouse_connect.get_client(
            host=parts.hostname or "localhost",
            port=parts.port or (8443 if secure else 8123),
            username=unquote(parts.username) if parts.username else "default",
            password=unquote(parts.password) if parts.password else "",
            secure=secure,
            autogenerate_session_id=False,
        )

    # ---- schema

    def ensure_schema(self) -> None:
        self._client.command(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
        self._client.command(
            f"""
CREATE TABLE IF NOT EXISTS {self.table} (
    `project_id` String,
    `run_id` String,
    `key` LowCardinality(String),
    `step` UInt64 CODEC(Delta, ZSTD(1)),
    `ts` DateTime64(6, 'UTC') CODEC(Delta, ZSTD(1)),
    `value` Float64 CODEC(Gorilla, ZSTD(1)),
    `version` UInt64 CODEC(Delta, ZSTD(1))
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, run_id, key, step)
SETTINGS index_granularity = 8192
"""
        )

    def drop_database(self) -> None:
        self._client.command(f"DROP DATABASE IF EXISTS `{self.database}`")

    # ---- writes

    def _next_versions(self, count: int) -> int:
        with self._version_lock:
            first = max(time.time_ns(), self._last_version + 1)
            self._last_version = first + count - 1
            return first

    def insert(self, project_id: str, run_id: str, points: Sequence[MetricPoint]) -> None:
        if not points:
            return
        if not project_id or not run_id:
            raise ValueError("points need a project_id and run_id")
        first = self._next_versions(len(points))
        # Later points of a batch get higher versions: in-batch duplicates resolve to the last.
        rows = [
            [project_id, run_id, p.key, int(p.step), int(p.ts_us), float(p.value), first + i]
            for i, p in enumerate(points)
        ]
        self._client.insert(self.table, rows, column_names=list(_COLUMNS), settings=self._insert_settings)

    def delete_run(self, project_id: str, run_id: str, *, wait: bool = False) -> None:
        self._client.command(
            f"DELETE FROM {self.table} WHERE project_id = {{project_id:String}} AND run_id = {{run_id:String}}",
            parameters={"project_id": project_id, "run_id": run_id},
            settings={"lightweight_deletes_sync": 2 if wait else 0},
        )

    # ---- reads

    def _rows(self, sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        return self._client.query(sql, parameters=params).result_rows

    def keys(self, project_id: str, run_id: str, limit: int = 1000) -> list[str]:
        rows = self._rows(
            f"""
            SELECT DISTINCT key FROM {self.table}
            WHERE project_id = {{project_id:String}} AND run_id = {{run_id:String}}
            ORDER BY key LIMIT {{limit:UInt32}}
            """,
            {"project_id": project_id, "run_id": run_id, "limit": max(0, int(limit))},
        )
        return [r[0] for r in rows]

    def key_stats(self, project_id: str, run_id: str) -> list[KeyStats]:
        rows = self._rows(
            f"""
            SELECT key, count() AS n, min(step), max(step), argMax(value, step),
                   minIf(value, isFinite(value)), maxIf(value, isFinite(value)), countIf(isFinite(value))
            FROM (
                SELECT key, step, value
                FROM {self.table} FINAL
                WHERE project_id = {{project_id:String}} AND run_id = {{run_id:String}}
            )
            GROUP BY key
            ORDER BY key
            """,
            {"project_id": project_id, "run_id": run_id},
        )
        return [
            KeyStats(
                key=key,
                count=int(n),
                first_step=int(first),
                last_step=int(last),
                last_value=finite_or_none(last_value),
                min=float(low) if nfin else None,
                max=float(high) if nfin else None,
            )
            for key, n, first, last, last_value, low, high, nfin in rows
        ]

    def read_series(
        self,
        project_id: str,
        run_ids: Sequence[str],
        keys: Sequence[str],
        *,
        max_points: int = DEFAULT_MAX_POINTS,
        x: Axis = "step",
        from_step: int | None = None,
        to_step: int | None = None,
    ) -> tuple[SeriesMap, bool]:
        max_points = clamp_max_points(max_points)
        if not run_ids or not keys:
            return {}, False
        params: dict[str, Any] = {
            "project_id": project_id,
            "run_ids": list(dict.fromkeys(run_ids)),
            "keys": list(dict.fromkeys(keys)),
            "mp": max_points,
            "nb": max_points // 4,
        }
        where = [
            "project_id = {project_id:String}",
            "run_id IN {run_ids:Array(String)}",
            "key IN {keys:Array(String)}",
        ]
        if from_step is not None:
            params["from_step"] = int(from_step)
            where.append("step >= {from_step:UInt64}")
        if to_step is not None:
            params["to_step"] = int(to_step)
            where.append("step <= {to_step:UInt64}")
        axis = "ts_us" if x == "time" else "toInt64(step)"
        sql = f"""
            SELECT run_id, key, any(n) AS n,
                   argMin(pt, tuple(a, step)) AS p_first,
                   argMax(pt, tuple(a, step)) AS p_last,
                   argMinIf(pt, tuple(value, step), isFinite(value)) AS p_min,
                   argMaxIf(pt, tuple(value, -toInt64(step)), isFinite(value)) AS p_max,
                   countIf(isFinite(value)) AS nfin
            FROM (
                SELECT run_id, key, step, value, a, n, tuple(step, value, ts_us) AS pt,
                       if(n <= {{mp:UInt32}}, step,
                          intDiv(toUInt64(a - lo) * {{nb:UInt32}}, toUInt64(hi - lo) + 1)) AS bucket
                FROM (
                    SELECT run_id, key, step, value, ts_us, {axis} AS a,
                           count() OVER w AS n, min({axis}) OVER w AS lo, max({axis}) OVER w AS hi
                    FROM (
                        SELECT run_id, key, step, value, toUnixTimestamp64Micro(ts) AS ts_us
                        FROM {self.table} FINAL
                        WHERE {" AND ".join(where)}
                    )
                    WINDOW w AS (PARTITION BY run_id, key)
                )
            )
            GROUP BY run_id, key, bucket
        """
        grouped: dict[tuple[str, str], list[_Bucket]] = {}
        for run_id, key, n, p_first, p_last, p_min, p_max, nfin in self._rows(sql, params):
            n = int(n)
            first, last = _ch_point(p_first), _ch_point(p_last)
            low = _ch_point(p_min) if nfin else None
            high = _ch_point(p_max) if nfin else None
            if n <= max_points:  # every point is its own bucket: no extrema needed
                low = high = None
                n = 0
            grouped.setdefault((run_id, key), []).append((n, first, last, low, high))
        result: SeriesMap = {}
        downsampled = False
        for (run_id, key), buckets in grouped.items():
            series, down = _assemble(buckets, x)
            result.setdefault(run_id, {})[key] = series
            downsampled = downsampled or down
        return result, downsampled

    def summaries(
        self, project_id: str, run_ids: Sequence[str], max_keys: int = MAX_SUMMARY_KEYS
    ) -> dict[str, dict[str, float | None]]:
        if not run_ids:
            return {}
        rows = self._rows(
            f"""
            SELECT run_id, key, argMax(value, tuple(step, version)) AS v
            FROM {self.table}
            WHERE project_id = {{project_id:String}} AND run_id IN {{run_ids:Array(String)}}
              AND NOT startsWith(key, {{sys:String}})
            GROUP BY run_id, key
            ORDER BY run_id, key
            LIMIT {{max_keys:UInt32}} BY run_id
            """,
            {
                "project_id": project_id,
                "run_ids": list(dict.fromkeys(run_ids)),
                "sys": SYS_PREFIX,
                "max_keys": max(0, int(max_keys)),
            },
        )
        out: dict[str, dict[str, float | None]] = {}
        for run_id, key, value in rows:
            out.setdefault(run_id, {})[key] = finite_or_none(value)
        return out


def build_metric_store(settings: Settings) -> MetricStore:
    """ClickHouse when `settings.span_store` is `clickhouse` (same server and database), else memory."""
    if settings.span_store == "clickhouse":
        store = ClickHouseMetricStore(settings.clickhouse_url, settings.clickhouse_database)
        store.ensure_schema()
        return store
    return MemoryMetricStore()


__all__ = [
    "ClickHouseMetricStore",
    "KeyStats",
    "MemoryMetricStore",
    "MetricPoint",
    "MetricStore",
    "Series",
    "build_metric_store",
]
