"""Span storage: the `SpanStore` protocol, an in-memory store and a ClickHouse store.

Every read and write is scoped by `project_id`. Trace summaries are derived from spans:
root = span without parent (earliest one on ties; earliest span when none is parentless),
status = "error" if any span errored, tokens/cost summed, previews = root input/output cut
to 300 chars, session/user from the root or else the earliest span that has them, tags =
sorted union. Pagination cursors are opaque base64 of (time in µs, id), descending.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote, urlsplit

from matrix_server.config import Settings
from matrix_server.domain.spans import SessionSummary, Span, SpanKind, TraceSummary

PREVIEW_CHARS = 300
MAX_LIMIT = 1000
# `since` filters on trace start; spans are pre-filtered with this slack so the partition and
# primary-key pruning stay exact for traces shorter than the slack.
_SINCE_SLACK = timedelta(hours=24)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class InvalidCursor(ValueError):
    """The pagination cursor is malformed."""


@runtime_checkable
class SpanStore(Protocol):
    def insert(self, spans: list[Span]) -> None: ...

    def list_traces(
        self,
        project_id: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        name: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[TraceSummary], str | None]: ...

    def get_trace(self, project_id: str, trace_id: str) -> tuple[TraceSummary, list[Span]] | None: ...

    def list_sessions(
        self, project_id: str, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[SessionSummary], str | None]: ...

    def get_session(
        self, project_id: str, session_id: str
    ) -> tuple[SessionSummary, list[TraceSummary]] | None: ...

    def overview(self, project_id: str, since: datetime | None = None) -> dict[str, Any]: ...

    def get_span(self, project_id: str, trace_id: str, span_id: str) -> Span | None: ...


# --------------------------------------------------------------------------- shared helpers


def to_micros(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = value - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_micros(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=int(micros))


def encode_cursor(time: datetime, key: str) -> str:
    raw = json.dumps([to_micros(time), key], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        micros, key = json.loads(raw)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidCursor("invalid cursor") from exc
    if not isinstance(micros, int) or isinstance(micros, bool) or not isinstance(key, str):
        raise InvalidCursor("invalid cursor")
    return micros, key


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _root_key(span: Span) -> tuple[bool, datetime, str]:
    return (span.parent_span_id is not None, span.start_time, span.span_id)


def _preview(value: str | None) -> str | None:
    return None if value is None else value[:PREVIEW_CHARS]


def summarize_trace(spans: Sequence[Span]) -> TraceSummary:
    if not spans:
        raise ValueError("a trace needs at least one span")
    ordered = sorted(spans, key=_root_key)
    root = ordered[0]
    start = min(s.start_time for s in spans)
    end = max(s.end_time for s in spans)
    return TraceSummary(
        trace_id=root.trace_id,
        name=root.name,
        session_id=next((s.session_id for s in ordered if s.session_id), None),
        user_id=next((s.user_id for s in ordered if s.user_id), None),
        start_time=start,
        end_time=end,
        duration_ms=(to_micros(end) - to_micros(start)) / 1000,
        status="error" if any(s.status == "error" for s in spans) else "ok",
        span_count=len(spans),
        input_tokens=sum(s.input_tokens for s in spans),
        output_tokens=sum(s.output_tokens for s in spans),
        total_cost_usd=sum(s.cost_usd for s in spans),
        root_kind=root.kind,
        input_preview=_preview(root.input),
        output_preview=_preview(root.output),
        tags=sorted({t for s in spans for t in s.tags}),
    )


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation (numpy default, ClickHouse quantileExactInclusive)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _trace_matches(
    summary: TraceSummary,
    *,
    session_id: str | None,
    user_id: str | None,
    name: str | None,
    status: str | None,
    kind: str | None,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if session_id is not None and summary.session_id != session_id:
        return False
    if user_id is not None and summary.user_id != user_id:
        return False
    if name is not None and summary.name != name:
        return False
    if status is not None and summary.status != status:
        return False
    if kind is not None and summary.root_kind.value != kind:
        return False
    if since is not None and summary.start_time < since:
        return False
    return until is None or summary.start_time <= until


def _empty_overview() -> dict[str, Any]:
    return {
        "trace_count": 0,
        "error_rate": 0.0,
        "p50_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "total_cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "by_model": [],
    }


# --------------------------------------------------------------------------- memory


class MemorySpanStore:
    """Thread-safe in-memory store for tests and dev mode. Re-inserting a span id replaces it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, dict[str, Span]]] = {}

    def insert(self, spans: list[Span]) -> None:
        with self._lock:
            for span in spans:
                if not span.project_id:
                    raise ValueError("span without project_id")
                trace = self._data.setdefault(span.project_id, {}).setdefault(span.trace_id, {})
                trace[span.span_id] = span

    def _summaries(self, project_id: str) -> list[TraceSummary]:
        with self._lock:
            traces = [list(t.values()) for t in self._data.get(project_id, {}).values()]
        return [summarize_trace(spans) for spans in traces if spans]

    def list_traces(
        self,
        project_id: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        name: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[TraceSummary], str | None]:
        limit = _clamp_limit(limit)
        after = decode_cursor(cursor) if cursor else None
        items = [
            s
            for s in self._summaries(project_id)
            if _trace_matches(
                s,
                session_id=session_id,
                user_id=user_id,
                name=name,
                status=status,
                kind=kind,
                since=since,
                until=until,
            )
        ]
        items.sort(key=lambda s: (to_micros(s.start_time), s.trace_id), reverse=True)
        if after is not None:
            items = [s for s in items if (to_micros(s.start_time), s.trace_id) < after]
        return _page(items, limit, lambda s: encode_cursor(s.start_time, s.trace_id))

    def get_trace(self, project_id: str, trace_id: str) -> tuple[TraceSummary, list[Span]] | None:
        with self._lock:
            spans = list(self._data.get(project_id, {}).get(trace_id, {}).values())
        if not spans:
            return None
        spans.sort(key=lambda s: (s.start_time, s.span_id))
        return summarize_trace(spans), spans

    def _sessions(self, project_id: str) -> list[SessionSummary]:
        by_session: dict[str, list[TraceSummary]] = {}
        for summary in self._summaries(project_id):
            if summary.session_id:
                by_session.setdefault(summary.session_id, []).append(summary)
        sessions = []
        for sid, traces in by_session.items():
            traces.sort(key=lambda s: (s.start_time, s.trace_id))
            sessions.append(
                SessionSummary(
                    session_id=sid,
                    user_id=next((t.user_id for t in traces if t.user_id), None),
                    trace_count=len(traces),
                    first_seen=min(t.start_time for t in traces),
                    last_seen=max(t.end_time for t in traces),
                    total_cost_usd=sum(t.total_cost_usd for t in traces),
                )
            )
        return sessions

    def list_sessions(
        self, project_id: str, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[SessionSummary], str | None]:
        limit = _clamp_limit(limit)
        after = decode_cursor(cursor) if cursor else None
        items = sorted(
            self._sessions(project_id), key=lambda s: (to_micros(s.last_seen), s.session_id), reverse=True
        )
        if after is not None:
            items = [s for s in items if (to_micros(s.last_seen), s.session_id) < after]
        return _page(items, limit, lambda s: encode_cursor(s.last_seen, s.session_id))

    def get_session(
        self, project_id: str, session_id: str
    ) -> tuple[SessionSummary, list[TraceSummary]] | None:
        summary = next((s for s in self._sessions(project_id) if s.session_id == session_id), None)
        if summary is None:
            return None
        traces, _ = self.list_traces(project_id, session_id=session_id, limit=MAX_LIMIT)
        return summary, traces

    def overview(self, project_id: str, since: datetime | None = None) -> dict[str, Any]:
        summaries = [s for s in self._summaries(project_id) if since is None or s.start_time >= since]
        if not summaries:
            return _empty_overview()
        trace_ids = {s.trace_id for s in summaries}
        with self._lock:
            spans = [
                span
                for tid, trace in self._data.get(project_id, {}).items()
                if tid in trace_ids
                for span in trace.values()
            ]
        models: dict[str, list[float]] = {}
        for span in spans:
            if span.model:
                models.setdefault(span.model, []).append(span.cost_usd)
        durations = [s.duration_ms for s in summaries]
        by_model = [{"model": m, "calls": len(c), "cost_usd": sum(c)} for m, c in models.items()]
        by_model.sort(key=lambda m: (-m["cost_usd"], m["model"]))
        return {
            "trace_count": len(summaries),
            "error_rate": sum(1 for s in summaries if s.status == "error") / len(summaries),
            "p50_latency_ms": percentile(durations, 0.5),
            "p95_latency_ms": percentile(durations, 0.95),
            "total_cost_usd": sum(s.total_cost_usd for s in summaries),
            "input_tokens": sum(s.input_tokens for s in summaries),
            "output_tokens": sum(s.output_tokens for s in summaries),
            "by_model": by_model,
        }

    def get_span(self, project_id: str, trace_id: str, span_id: str) -> Span | None:
        with self._lock:
            return self._data.get(project_id, {}).get(trace_id, {}).get(span_id)


def _page(items: list[Any], limit: int, make_cursor: Any) -> tuple[list[Any], str | None]:
    if len(items) > limit:
        page = items[:limit]
        return page, make_cursor(page[-1])
    return items, None


# --------------------------------------------------------------------------- ClickHouse

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_JSON_COLUMNS = (
    "input_messages",
    "output_messages",
    "documents",
    "tool",
    "media",
    "invocation_parameters",
    "attributes",
    "events",
)
# (name, type); order is the insert order.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("project_id", "String"),
    ("trace_id", "String"),
    ("span_id", "String"),
    ("parent_span_id", "String"),
    ("name", "String"),
    ("kind", "LowCardinality(String)"),
    ("start_time", "DateTime64(6, 'UTC')"),
    ("end_time", "DateTime64(6, 'UTC')"),
    ("duration_ms", "Float64"),
    ("status", "LowCardinality(String)"),
    ("status_message", "Nullable(String)"),
    ("model", "LowCardinality(String)"),
    ("provider", "LowCardinality(String)"),
    ("input_tokens", "UInt64"),
    ("output_tokens", "UInt64"),
    ("reasoning_tokens", "UInt64"),
    ("cache_read_tokens", "UInt64"),
    ("cache_write_tokens", "UInt64"),
    ("cost_usd", "Float64"),
    ("ttft_ms", "Nullable(Float64)"),
    ("input", "Nullable(String) CODEC(ZSTD(3))"),
    ("output", "Nullable(String) CODEC(ZSTD(3))"),
    ("input_messages", "String CODEC(ZSTD(3))"),
    ("output_messages", "String CODEC(ZSTD(3))"),
    ("documents", "String CODEC(ZSTD(3))"),
    ("tool", "String CODEC(ZSTD(3))"),
    ("media", "String CODEC(ZSTD(3))"),
    ("invocation_parameters", "String CODEC(ZSTD(3))"),
    ("attributes", "String CODEC(ZSTD(3))"),
    ("events", "String CODEC(ZSTD(3))"),
    ("session_id", "String"),
    ("user_id", "String"),
    ("tags", "Array(String)"),
)
_COLUMN_NAMES = [name for name, _ in _COLUMNS]
_TIME_COLUMNS = ("start_time", "end_time")
_SELECT_SPAN = ", ".join(
    f"toUnixTimestamp64Micro({c}) AS {c}" if c in _TIME_COLUMNS else c for c in _COLUMN_NAMES[1:]
)
# Root ordering key: parentless first, then earliest, then span id (same as `_root_key`).
_ROOT_KEY = "tuple(parent_span_id != '', start_time, span_id)"
_TRACE_AGG = f"""
    toUnixTimestamp64Micro(min(start_time)) AS ts,
    toUnixTimestamp64Micro(max(end_time)) AS te,
    count() AS span_count,
    sum(input_tokens) AS in_tok,
    sum(output_tokens) AS out_tok,
    sum(cost_usd) AS cost,
    max(status = 'error') AS has_error,
    argMin(name, {_ROOT_KEY}) AS root_name,
    argMin(kind, {_ROOT_KEY}) AS root_kind,
    argMin(tuple(leftUTF8(input, {PREVIEW_CHARS})), {_ROOT_KEY}).1 AS input_preview,
    argMin(tuple(leftUTF8(output, {PREVIEW_CHARS})), {_ROOT_KEY}).1 AS output_preview,
    argMinIf(session_id, {_ROOT_KEY}, session_id != '') AS sid,
    argMinIf(user_id, {_ROOT_KEY}, user_id != '') AS uid,
    arraySort(groupUniqArrayArray(tags)) AS all_tags
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _span_row(span: Span) -> list[Any]:
    data = span.model_dump(mode="json")
    return [
        span.project_id,
        span.trace_id,
        span.span_id,
        span.parent_span_id or "",
        span.name,
        span.kind.value,
        span.start_time,
        span.end_time,
        span.duration_ms,
        span.status,
        span.status_message,
        span.model or "",
        span.provider or "",
        span.input_tokens,
        span.output_tokens,
        span.reasoning_tokens,
        span.cache_read_tokens,
        span.cache_write_tokens,
        span.cost_usd,
        span.ttft_ms,
        span.input,
        span.output,
        _dumps(data["input_messages"]),
        _dumps(data["output_messages"]),
        _dumps(data["documents"]),
        _dumps(data["tool"]) if span.tool is not None else "",
        _dumps(data["media"]),
        _dumps(data["invocation_parameters"]),
        _dumps(data["attributes"]),
        _dumps(data["events"]),
        span.session_id or "",
        span.user_id or "",
        list(span.tags),
    ]


def _span_from_row(project_id: str, row: dict[str, Any]) -> Span:
    data = dict(row)
    for col in _TIME_COLUMNS:
        data[col] = from_micros(data[col])
    for col in _JSON_COLUMNS:
        data[col] = json.loads(data[col]) if data[col] else None
    for col in ("parent_span_id", "model", "provider", "session_id", "user_id"):
        data[col] = data[col] or None
    for col in ("input_messages", "output_messages", "documents", "media", "events"):
        data[col] = data[col] or []
    for col in ("invocation_parameters", "attributes"):
        data[col] = data[col] or {}
    data["project_id"] = project_id
    return Span.model_validate(data)


def _summary_from_row(row: dict[str, Any]) -> TraceSummary:
    try:
        root_kind = SpanKind(row["root_kind"])
    except ValueError:
        root_kind = SpanKind.UNKNOWN
    return TraceSummary(
        trace_id=row["trace_id"],
        name=row["root_name"],
        session_id=row["sid"] or None,
        user_id=row["uid"] or None,
        start_time=from_micros(row["ts"]),
        end_time=from_micros(row["te"]),
        duration_ms=(row["te"] - row["ts"]) / 1000,
        status="error" if row["has_error"] else "ok",
        span_count=int(row["span_count"]),
        input_tokens=int(row["in_tok"]),
        output_tokens=int(row["out_tok"]),
        total_cost_usd=float(row["cost"]),
        root_kind=root_kind,
        input_preview=row["input_preview"],
        output_preview=row["output_preview"],
        tags=list(row["all_tags"]),
    )


def _finite(value: Any) -> float:
    number = float(value or 0.0)
    return 0.0 if math.isnan(number) or math.isinf(number) else number


class ClickHouseSpanStore:
    """Spans in a ClickHouse `MergeTree` table, read with parameterized queries only.

    Layout: `ORDER BY (project_id, trace_id, start_time, span_id)` makes a trace a contiguous
    range of the primary key, so trace/span lookups and the per-page aggregation
    (`trace_id IN (...)`) read only the needed granules. `list_traces` first ranks trace ids
    with a `GROUP BY trace_id` over light columns of one project, then aggregates the heavy
    columns (previews, tags) only for the page. Partitions by month prune `since` filters.
    """

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
        self.table = f"`{database}`.`spans`"
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

    # ---- schema

    def ensure_schema(self) -> None:
        columns = ",\n    ".join(f"`{name}` {ctype}" for name, ctype in _COLUMNS)
        ttl = ""
        if self.ttl_days is not None:
            ttl = f"\nTTL toDateTime(start_time) + INTERVAL {int(self.ttl_days)} DAY"
        self._client.command(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
        self._client.command(
            f"""
CREATE TABLE IF NOT EXISTS {self.table} (
    {columns},
    `inserted_at` DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_session session_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_user user_id TYPE bloom_filter(0.01) GRANULARITY 4,
    INDEX idx_model model TYPE set(256) GRANULARITY 4
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(start_time)
ORDER BY (project_id, trace_id, start_time, span_id){ttl}
SETTINGS index_granularity = 8192
"""
        )

    def drop_database(self) -> None:
        self._client.command(f"DROP DATABASE IF EXISTS `{self.database}`")

    # ---- writes

    def insert(self, spans: list[Span]) -> None:
        if not spans:
            return
        for span in spans:
            if not span.project_id:
                raise ValueError("span without project_id")
        self._client.insert(
            self.table,
            [_span_row(s) for s in spans],
            column_names=_COLUMN_NAMES,
            settings=self._insert_settings,
        )

    # ---- reads

    def _rows(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        result = self._client.query(sql, parameters=params)
        names = result.column_names
        return [dict(zip(names, row, strict=True)) for row in result.result_rows]

    def list_traces(
        self,
        project_id: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        name: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[TraceSummary], str | None]:
        limit = _clamp_limit(limit)
        params: dict[str, Any] = {"project_id": project_id, "limit": limit + 1}
        where = ["project_id = {project_id:String}"]
        having = ["1"]
        if since is not None:
            params["since"] = to_micros(since)
            params["since_pruned"] = to_micros(since - _SINCE_SLACK)
            where.append("start_time >= fromUnixTimestamp64Micro({since_pruned:Int64}, 'UTC')")
            having.append("ts >= {since:Int64}")
        if until is not None:
            params["until"] = to_micros(until)
            having.append("ts <= {until:Int64}")
        if session_id is not None:
            params["session_id"] = session_id
            having.append("sid = {session_id:String}")
        if user_id is not None:
            params["user_id"] = user_id
            having.append("uid = {user_id:String}")
        if name is not None:
            params["name"] = name
            having.append("root_name = {name:String}")
        if kind is not None:
            params["kind"] = kind
            having.append("root_kind = {kind:String}")
        if status is not None:
            if status not in ("ok", "error"):
                return [], None
            params["has_error"] = 1 if status == "error" else 0
            having.append("has_error = {has_error:UInt8}")
        if cursor:
            params["cursor_ts"], params["cursor_id"] = decode_cursor(cursor)
            having.append("(ts, trace_id) < ({cursor_ts:Int64}, {cursor_id:String})")
        ranked = f"""
            SELECT trace_id
            FROM (
                SELECT
                    trace_id,
                    toUnixTimestamp64Micro(min(start_time)) AS ts,
                    max(status = 'error') AS has_error,
                    argMin(name, {_ROOT_KEY}) AS root_name,
                    argMin(kind, {_ROOT_KEY}) AS root_kind,
                    argMinIf(session_id, {_ROOT_KEY}, session_id != '') AS sid,
                    argMinIf(user_id, {_ROOT_KEY}, user_id != '') AS uid
                FROM {self.table}
                WHERE {" AND ".join(where)}
                GROUP BY trace_id
                HAVING {" AND ".join(having)}
                ORDER BY ts DESC, trace_id DESC
                LIMIT {{limit:UInt32}}
            )
        """
        sql = f"""
            SELECT trace_id, {_TRACE_AGG}
            FROM {self.table}
            WHERE project_id = {{project_id:String}} AND trace_id IN ({ranked})
            GROUP BY trace_id
            ORDER BY ts DESC, trace_id DESC
        """
        items = [_summary_from_row(r) for r in self._rows(sql, params)]
        return _page(items, limit, lambda s: encode_cursor(s.start_time, s.trace_id))

    def _spans(self, project_id: str, trace_id: str, span_id: str | None = None) -> list[Span]:
        params = {"project_id": project_id, "trace_id": trace_id}
        span_filter = ""
        if span_id is not None:
            params["span_id"] = span_id
            span_filter = "AND span_id = {span_id:String}"
        sql = f"""
            SELECT {_SELECT_SPAN}
            FROM {self.table}
            WHERE project_id = {{project_id:String}} AND trace_id = {{trace_id:String}} {span_filter}
            ORDER BY inserted_at
        """
        # A retried export can store a span twice; the latest insert wins.
        unique: dict[str, Span] = {}
        for row in self._rows(sql, params):
            unique[row["span_id"]] = _span_from_row(project_id, row)
        return sorted(unique.values(), key=lambda s: (s.start_time, s.span_id))

    def get_trace(self, project_id: str, trace_id: str) -> tuple[TraceSummary, list[Span]] | None:
        spans = self._spans(project_id, trace_id)
        if not spans:
            return None
        return summarize_trace(spans), spans

    def get_span(self, project_id: str, trace_id: str, span_id: str) -> Span | None:
        spans = self._spans(project_id, trace_id, span_id)
        return spans[0] if spans else None

    def _session_rows(
        self, project_id: str, *, session_id: str | None, limit: int, cursor: str | None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"project_id": project_id, "limit": limit}
        having = ["1"]
        if session_id is not None:
            params["session_id"] = session_id
            having.append("sid = {session_id:String}")
        if cursor:
            params["cursor_ts"], params["cursor_id"] = decode_cursor(cursor)
            having.append("(last_seen, sid) < ({cursor_ts:Int64}, {cursor_id:String})")
        sql = f"""
            SELECT
                sid,
                argMinIf(uid, tuple(ts, trace_id), uid != '') AS user_id,
                count() AS trace_count,
                min(ts) AS first_seen,
                max(te) AS last_seen,
                sum(cost) AS total_cost
            FROM (
                SELECT
                    trace_id,
                    toUnixTimestamp64Micro(min(start_time)) AS ts,
                    toUnixTimestamp64Micro(max(end_time)) AS te,
                    sum(cost_usd) AS cost,
                    argMinIf(session_id, {_ROOT_KEY}, session_id != '') AS sid,
                    argMinIf(user_id, {_ROOT_KEY}, user_id != '') AS uid
                FROM {self.table}
                WHERE project_id = {{project_id:String}} AND trace_id IN (
                    SELECT trace_id FROM {self.table}
                    WHERE project_id = {{project_id:String}} AND session_id != ''
                )
                GROUP BY trace_id
                HAVING sid != ''
            )
            GROUP BY sid
            HAVING {" AND ".join(having)}
            ORDER BY last_seen DESC, sid DESC
            LIMIT {{limit:UInt32}}
        """
        return self._rows(sql, params)

    @staticmethod
    def _session_from_row(row: dict[str, Any]) -> SessionSummary:
        return SessionSummary(
            session_id=row["sid"],
            user_id=row["user_id"] or None,
            trace_count=int(row["trace_count"]),
            first_seen=from_micros(row["first_seen"]),
            last_seen=from_micros(row["last_seen"]),
            total_cost_usd=float(row["total_cost"]),
        )

    def list_sessions(
        self, project_id: str, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[SessionSummary], str | None]:
        limit = _clamp_limit(limit)
        rows = self._session_rows(project_id, session_id=None, limit=limit + 1, cursor=cursor)
        items = [self._session_from_row(r) for r in rows]
        return _page(items, limit, lambda s: encode_cursor(s.last_seen, s.session_id))

    def get_session(
        self, project_id: str, session_id: str
    ) -> tuple[SessionSummary, list[TraceSummary]] | None:
        rows = self._session_rows(project_id, session_id=session_id, limit=1, cursor=None)
        if not rows:
            return None
        traces, _ = self.list_traces(project_id, session_id=session_id, limit=MAX_LIMIT)
        return self._session_from_row(rows[0]), traces

    def overview(self, project_id: str, since: datetime | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"project_id": project_id}
        where = "project_id = {project_id:String}"
        having = ""
        if since is not None:
            params["since"] = to_micros(since)
            params["since_pruned"] = to_micros(since - _SINCE_SLACK)
            where += " AND start_time >= fromUnixTimestamp64Micro({since_pruned:Int64}, 'UTC')"
            having = "HAVING ts >= {since:Int64}"
        traces = f"""
            SELECT
                trace_id,
                toUnixTimestamp64Micro(min(start_time)) AS ts,
                toUnixTimestamp64Micro(max(end_time)) AS te,
                max(status = 'error') AS has_error,
                sum(cost_usd) AS cost,
                sum(input_tokens) AS in_tok,
                sum(output_tokens) AS out_tok
            FROM {self.table}
            WHERE {where}
            GROUP BY trace_id
            {having}
        """
        totals = self._rows(
            f"""
            SELECT
                count() AS trace_count,
                avg(has_error) AS error_rate,
                quantileExactInclusive(0.5)((te - ts) / 1000) AS p50,
                quantileExactInclusive(0.95)((te - ts) / 1000) AS p95,
                sum(cost) AS total_cost,
                sum(in_tok) AS input_tokens,
                sum(out_tok) AS output_tokens
            FROM ({traces})
            """,
            params,
        )[0]
        if not totals["trace_count"]:
            return _empty_overview()
        trace_filter = f"AND trace_id IN (SELECT trace_id FROM ({traces}))" if since is not None else ""
        models = self._rows(
            f"""
            SELECT model, count() AS calls, sum(cost_usd) AS model_cost
            FROM {self.table}
            WHERE project_id = {{project_id:String}} AND model != '' {trace_filter}
            GROUP BY model
            ORDER BY model_cost DESC, model ASC
            """,
            params,
        )
        return {
            "trace_count": int(totals["trace_count"]),
            "error_rate": _finite(totals["error_rate"]),
            "p50_latency_ms": _finite(totals["p50"]),
            "p95_latency_ms": _finite(totals["p95"]),
            "total_cost_usd": _finite(totals["total_cost"]),
            "input_tokens": int(totals["input_tokens"]),
            "output_tokens": int(totals["output_tokens"]),
            "by_model": [
                {"model": m["model"], "calls": int(m["calls"]), "cost_usd": float(m["model_cost"])}
                for m in models
            ],
        }


def build_span_store(settings: Settings) -> SpanStore:
    """Store selected by `settings.span_store`; the ClickHouse schema is created if missing."""
    if settings.span_store == "clickhouse":
        store = ClickHouseSpanStore(settings.clickhouse_url, settings.clickhouse_database)
        store.ensure_schema()
        return store
    return MemorySpanStore()


__all__ = [
    "ClickHouseSpanStore",
    "InvalidCursor",
    "MemorySpanStore",
    "SpanStore",
    "build_span_store",
    "decode_cursor",
    "encode_cursor",
    "summarize_trace",
]
