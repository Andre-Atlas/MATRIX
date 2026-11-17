"""Training metric time-series storage: `TrainingMetricStore` protocol, in-memory and ClickHouse stores.

Every read and write is scoped by `project_id`. Series exceeding `max_points` are deterministically
downsampled by `step` buckets, returning `{metric_name: [{step, value, min, max, epoch, recorded_at}]}`.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

from matrix_server.config import Settings

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_COLUMN_NAMES = [
    "project_id",
    "run_id",
    "category",
    "metric_name",
    "step",
    "epoch",
    "value",
    "recorded_at",
]


@runtime_checkable
class TrainingMetricStore(Protocol):
    def insert_points(self, project_id: str, run_id: str, points: list[dict[str, Any]]) -> int: ...

    def query_metrics(
        self,
        project_id: str,
        run_id: str,
        *,
        metric_names: list[str] | None = None,
        category: str | None = None,
        min_step: int | None = None,
        max_step: int | None = None,
        max_points: int = 500,
    ) -> dict[str, list[dict[str, Any]]]: ...


@dataclass(frozen=True, slots=True)
class _MetricPoint:
    category: str
    metric_name: str
    step: int
    epoch: float | None
    value: float
    recorded_at: datetime


def _parse_recorded_at(raw: Any) -> datetime:
    if raw is None:
        return datetime.now(UTC)
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
    if isinstance(raw, str):
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    raise ValueError("recorded_at must be a datetime, ISO string, or None")


def _finite_float(raw: Any, field: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    val = float(raw)
    if not math.isfinite(val):
        raise ValueError(f"{field} must be finite (NaN/Infinity not allowed)")
    return val


def _normalize_point(point: dict[str, Any]) -> _MetricPoint:
    metric_name = point.get("metric_name")
    if not isinstance(metric_name, str) or not metric_name.strip():
        raise ValueError("metric_name must be a non-empty string")
    category = point.get("category") or "train"
    if not isinstance(category, str) or not category.strip():
        raise ValueError("category must be a non-empty string")
    step = point.get("step")
    if isinstance(step, bool) or not isinstance(step, int):
        raise TypeError("step must be an integer")
    value = _finite_float(point.get("value"), "value")
    raw_epoch = point.get("epoch")
    epoch = _finite_float(raw_epoch, "epoch") if raw_epoch is not None else None
    recorded_at = _parse_recorded_at(point.get("recorded_at"))
    return _MetricPoint(
        category=category.strip(),
        metric_name=metric_name.strip(),
        step=step,
        epoch=epoch,
        value=value,
        recorded_at=recorded_at,
    )


def _clamp_max_points(max_points: int) -> int:
    return max(1, min(int(max_points), 5000))


def _downsample_series(series: list[_MetricPoint], max_points: int) -> list[dict[str, Any]]:
    ordered = sorted(series, key=lambda p: (p.step, p.recorded_at))
    if not ordered:
        return []
    limit = _clamp_max_points(max_points)
    if len(ordered) <= limit:
        return [
            {
                "step": p.step,
                "value": p.value,
                "min": p.value,
                "max": p.value,
                "epoch": p.epoch,
                "recorded_at": p.recorded_at,
            }
            for p in ordered
        ]

    min_s = ordered[0].step
    max_s = ordered[-1].step
    span = max_s - min_s + 1
    bucket_size = 1 if span <= limit else math.ceil(span / limit)

    buckets: dict[int, list[_MetricPoint]] = {}
    for p in ordered:
        bid = (p.step - min_s) // bucket_size
        buckets.setdefault(bid, []).append(p)

    out: list[dict[str, Any]] = []
    for bid in sorted(buckets):
        pts = buckets[bid]
        last_epoch = next((p.epoch for p in reversed(pts) if p.epoch is not None), None)
        out.append(
            {
                "step": max(p.step for p in pts),
                "value": sum(p.value for p in pts) / len(pts),
                "min": min(p.value for p in pts),
                "max": max(p.value for p in pts),
                "epoch": last_epoch,
                "recorded_at": max(p.recorded_at for p in pts),
            }
        )
    return out


def _from_micros(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=int(micros))


class MemoryTrainingMetricStore:
    """Thread-safe in-memory training metric store for tests and dev mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple[str, str], list[_MetricPoint]] = {}

    def insert_points(self, project_id: str, run_id: str, points: list[dict[str, Any]]) -> int:
        if not project_id or not run_id:
            raise ValueError("project_id and run_id are required")
        normalized = [_normalize_point(p) for p in points]
        if not normalized:
            return 0
        with self._lock:
            self._data.setdefault((project_id, run_id), []).extend(normalized)
        return len(normalized)

    def query_metrics(
        self,
        project_id: str,
        run_id: str,
        *,
        metric_names: list[str] | None = None,
        category: str | None = None,
        min_step: int | None = None,
        max_step: int | None = None,
        max_points: int = 500,
    ) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            raw = list(self._data.get((project_id, run_id), ()))

        name_filter = set(metric_names) if metric_names is not None else None
        grouped: dict[str, list[_MetricPoint]] = {}
        for p in raw:
            if category is not None and p.category != category:
                continue
            if name_filter is not None and p.metric_name not in name_filter:
                continue
            if min_step is not None and p.step < min_step:
                continue
            if max_step is not None and p.step > max_step:
                continue
            grouped.setdefault(p.metric_name, []).append(p)

        return {
            name: _downsample_series(series, max_points)
            for name, series in sorted(grouped.items())
        }


class ClickHouseTrainingMetricStore:
    """Training metrics in a ClickHouse `MergeTree` table with ZSTD(3) compression."""

    def __init__(
        self,
        url: str,
        database: str = "matrix",
        *,
        ttl_days: int | None = None,
        async_insert: bool = True,
        client: Any = None,
    ) -> None:
        if not _IDENTIFIER.match(database):
            raise ValueError("invalid ClickHouse database name")
        self.database = database
        self.table = f"`{database}`.`training_metrics`"
        self.ttl_days = ttl_days
        self._insert_settings = {"async_insert": 1, "wait_for_async_insert": 1} if async_insert else {}
        self._client = client if client is not None else self._connect(url)

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

    def ensure_schema(self) -> None:
        ttl = ""
        if self.ttl_days is not None:
            ttl = f"\nTTL toDateTime(recorded_at) + INTERVAL {int(self.ttl_days)} DAY"
        self._client.command(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
        self._client.command(
            f"""
CREATE TABLE IF NOT EXISTS {self.table} (
    `project_id` String,
    `run_id` String,
    `category` LowCardinality(String),
    `metric_name` LowCardinality(String),
    `step` Int64,
    `epoch` Nullable(Float64),
    `value` Float64 CODEC(ZSTD(3)),
    `recorded_at` DateTime64(6, 'UTC') CODEC(ZSTD(3))
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(recorded_at)
ORDER BY (project_id, run_id, category, metric_name, step, recorded_at){ttl}
SETTINGS index_granularity = 8192
"""
        )

    def insert_points(self, project_id: str, run_id: str, points: list[dict[str, Any]]) -> int:
        if not project_id or not run_id:
            raise ValueError("project_id and run_id are required")
        normalized = [_normalize_point(p) for p in points]
        if not normalized:
            return 0
        rows = [
            [
                project_id,
                run_id,
                p.category,
                p.metric_name,
                p.step,
                p.epoch,
                p.value,
                p.recorded_at,
            ]
            for p in normalized
        ]
        self._client.insert(
            self.table,
            rows,
            column_names=_COLUMN_NAMES,
            settings=self._insert_settings,
        )
        return len(rows)

    def _rows(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        result = self._client.query(sql, parameters=params)
        names = result.column_names
        return [dict(zip(names, row, strict=True)) for row in result.result_rows]

    def query_metrics(
        self,
        project_id: str,
        run_id: str,
        *,
        metric_names: list[str] | None = None,
        category: str | None = None,
        min_step: int | None = None,
        max_step: int | None = None,
        max_points: int = 500,
    ) -> dict[str, list[dict[str, Any]]]:
        limit = _clamp_max_points(max_points)
        params: dict[str, Any] = {"project_id": project_id, "run_id": run_id}
        where = ["project_id = {project_id:String}", "run_id = {run_id:String}"]
        if category is not None:
            params["category"] = category
            where.append("category = {category:String}")
        if metric_names is not None:
            if not metric_names:
                return {}
            params["metric_names"] = list(metric_names)
            where.append("metric_name IN {metric_names:Array(String)}")
        if min_step is not None:
            params["min_step"] = int(min_step)
            where.append("step >= {min_step:Int64}")
        if max_step is not None:
            params["max_step"] = int(max_step)
            where.append("step <= {max_step:Int64}")
        where_sql = " AND ".join(where)

        stats = self._rows(
            f"""
            SELECT
                metric_name,
                count() AS cnt,
                min(step) AS min_s,
                max(step) AS max_s
            FROM {self.table}
            WHERE {where_sql}
            GROUP BY metric_name
            ORDER BY metric_name ASC
            """,
            params,
        )

        out: dict[str, list[dict[str, Any]]] = {}
        for stat in stats:
            mname = stat["metric_name"]
            cnt = int(stat["cnt"])
            min_s = int(stat["min_s"])
            max_s = int(stat["max_s"])
            m_params = {**params, "target_metric": mname}
            if cnt <= limit:
                rows = self._rows(
                    f"""
                    SELECT
                        step,
                        value,
                        epoch,
                        toUnixTimestamp64Micro(recorded_at) AS recorded_at_us
                    FROM {self.table}
                    WHERE {where_sql} AND metric_name = {{target_metric:String}}
                    ORDER BY step ASC, recorded_at ASC
                    """,
                    m_params,
                )
                out[mname] = [
                    {
                        "step": int(r["step"]),
                        "value": float(r["value"]),
                        "min": float(r["value"]),
                        "max": float(r["value"]),
                        "epoch": float(r["epoch"]) if r["epoch"] is not None else None,
                        "recorded_at": _from_micros(r["recorded_at_us"]),
                    }
                    for r in rows
                ]
            else:
                span = max_s - min_s + 1
                bucket_size = 1 if span <= limit else math.ceil(span / limit)
                m_params["min_s"] = min_s
                m_params["bucket_size"] = bucket_size
                rows = self._rows(
                    f"""
                    SELECT
                        max(step) AS step,
                        avg(value) AS value,
                        min(value) AS min_val,
                        max(value) AS max_val,
                        argMaxIf(epoch, tuple(step, recorded_at), epoch IS NOT NULL) AS epoch,
                        toUnixTimestamp64Micro(max(recorded_at)) AS recorded_at_us
                    FROM {self.table}
                    WHERE {where_sql} AND metric_name = {{target_metric:String}}
                    GROUP BY intDiv(step - {{min_s:Int64}}, {{bucket_size:Int64}})
                    ORDER BY step ASC
                    """,
                    m_params,
                )
                out[mname] = [
                    {
                        "step": int(r["step"]),
                        "value": float(r["value"]),
                        "min": float(r["min_val"]),
                        "max": float(r["max_val"]),
                        "epoch": float(r["epoch"]) if r["epoch"] is not None else None,
                        "recorded_at": _from_micros(r["recorded_at_us"]),
                    }
                    for r in rows
                ]
        return out


def build_training_metric_store(settings: Settings) -> TrainingMetricStore:
    """Store selected by `settings.span_store`; the ClickHouse schema is created if missing."""
    if settings.span_store == "clickhouse":
        store = ClickHouseTrainingMetricStore(settings.clickhouse_url, settings.clickhouse_database)
        store.ensure_schema()
        return store
    return MemoryTrainingMetricStore()


__all__ = [
    "ClickHouseTrainingMetricStore",
    "MemoryTrainingMetricStore",
    "TrainingMetricStore",
    "build_training_metric_store",
]
