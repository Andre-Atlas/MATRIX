"""Training-run tracking: `start_run()` / `Run` with a background, never-raising sender.

Wire format: contract G1 (`/api/v1/runs/...`) and the artifact store of contract G0 section 2.
"""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import mimetypes
import numbers
import os
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlsplit, urlunsplit

import httpx

from .client import MatrixError, _seg

if TYPE_CHECKING:
    from .client import MatrixClient

logger = logging.getLogger("matrix_sdk.tracking")

METRIC_KEY = re.compile(r"^[A-Za-z0-9_./:@ -]{1,200}$")
RUN_STATUSES = ("finished", "failed", "killed")
ARTIFACT_KINDS = ("file", "model", "plot", "table", "report")
REPORT_KINDS = ("classification", "regression", "importance", "table", "custom")
MAX_QUEUE = 100_000
BATCH_SIZE = 1000
FLUSH_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 30.0
MAX_PACKAGES = 500
MAX_TABLE_ROWS = 1000
MAX_CELL_CHARS = 2000
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_ERROR_CHARS = 1000
CHUNK = 1024 * 1024

_current: Run | None = None
_current_lock = threading.Lock()


# -- value conversion ---------------------------------------------------------------------------


def to_metric_value(value: Any, key: str = "value") -> float | str:
    """A finite float, or "nan" / "inf" / "-inf". Non-numeric values are a training bug: TypeError now.

    numpy / torch scalars and 0-d arrays are converted through `.item()` without importing either library.
    """
    original = value
    if isinstance(value, (str, bytes)) or value is None:
        raise TypeError(f"metric {key!r} must be a number, got {type(original).__name__}: {original!r:.80}")
    if not isinstance(value, numbers.Real) and hasattr(value, "item"):
        if getattr(value, "ndim", 0) != 0 and getattr(value, "numel", lambda: 0)() != 1:
            raise TypeError(f"metric {key!r} must be a scalar, got an array of shape "
                            f"{tuple(getattr(value, 'shape', ()))}")
        try:
            value = value.item()
        except (ValueError, RuntimeError) as exc:
            raise TypeError(f"metric {key!r} must be a scalar: {exc}") from None
    if isinstance(value, complex) or not isinstance(value, numbers.Real):
        raise TypeError(f"metric {key!r} must be a number, got {type(original).__name__}")
    value = float(value)
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value


def jsonable(value: Any, _depth: int = 0) -> Any:
    """Best-effort conversion to JSON (params, tables, report data); unknown objects become `str()`."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else to_metric_value(value)
    if _depth > 20:
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset, deque)):
        return [jsonable(v, _depth + 1) for v in value]
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value), _depth + 1)
    if hasattr(value, "item") and getattr(value, "ndim", 1) == 0:
        with contextlib.suppress(Exception):
            return jsonable(value.item(), _depth + 1)
    if hasattr(value, "tolist"):
        with contextlib.suppress(Exception):
            return jsonable(value.tolist(), _depth + 1)
    return str(value)


def _config_dict(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, Mapping):
        return dict(config)
    if dataclasses.is_dataclass(config) and not isinstance(config, type):
        return dataclasses.asdict(config)
    if hasattr(config, "to_dict"):
        return dict(config.to_dict())
    if hasattr(config, "__dict__"):
        return dict(vars(config))  # argparse.Namespace and plain objects
    raise TypeError(f"config must be a mapping, dataclass or namespace, got {type(config).__name__}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _short_error(exc: BaseException) -> str:
    """`Type: message`, never a traceback (it could carry local variables)."""
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return text[:MAX_ERROR_CHARS]


# -- capture --------------------------------------------------------------------------------------


def _git(args: list[str], cwd: str | None, timeout: float) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                             check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def strip_credentials(url: str | None) -> str | None:
    """Removes `user:token@` from http(s)/ssh URLs; scp-style `git@host:path` is kept (no secret in it)."""
    if not url:
        return url
    if "://" not in url:
        return url
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def capture_git(cwd: str | None = None, timeout: float = 2.0) -> dict[str, Any] | None:
    """Commit, branch, dirty flag and credential-free remote; None outside a repository or without git."""
    commit = _git(["rev-parse", "HEAD"], cwd, timeout)
    if commit is None:
        return None
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout)
    status = _git(["status", "--porcelain", "--untracked-files=no"], cwd, timeout)
    remote = _git(["config", "--get", "remote.origin.url"], cwd, timeout)
    return {
        "commit": commit,
        "branch": None if branch in (None, "HEAD") else branch,
        "dirty": None if status is None else bool(status),
        "remote": strip_credentials(remote),
    }


def capture_env(max_packages: int = MAX_PACKAGES) -> dict[str, Any]:
    """Python, platform, hostname and installed packages. Environment variables are never read."""
    packages: set[str] = set()
    try:
        for dist in metadata.distributions():
            name = dist.metadata["Name"] if dist.metadata else None
            if name:
                packages.add(f"{name}=={dist.version}")
    except Exception:
        logger.debug("package listing failed", exc_info=True)
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hostname": hostname,
        "packages": sorted(packages, key=str.lower)[:max_packages],
    }


# -- run ------------------------------------------------------------------------------------------


class Run:
    """A training run. `log*` calls only enqueue; a daemon thread ships them in batches."""

    def __init__(
        self,
        client: MatrixClient,
        data: dict[str, Any],
        *,
        owns_client: bool = False,
        flush_timeout: float = 30.0,
        max_queue: int = MAX_QUEUE,
        batch_size: int = BATCH_SIZE,
        flush_interval: float = FLUSH_INTERVAL,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
    ) -> None:
        self._client = client
        self._http = client._http
        self.data = data
        self.id: str = data["id"]
        self.name: str = data.get("name", "")
        self._owns_client = owns_client
        self.flush_timeout = flush_timeout
        self._max_queue = max_queue
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._heartbeat_interval = heartbeat_interval
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max

        self.dropped_points = 0
        self.status = "running"
        self._step = 0
        self._cv = threading.Condition()
        self._points: deque[dict[str, Any]] = deque()
        self._params: dict[str, Any] = {}
        self._ops: deque[tuple[str, str, Any]] = deque()
        self._busy = False
        self._closing = False
        self._deadline: float | None = None
        self._wake = threading.Event()  # interrupts backoff sleeps when finish() starts
        self._finish_lock = threading.Lock()
        self._warned: dict[str, int] = {}
        self._monitor: Any = None

        self._sender = threading.Thread(target=self._send_loop, daemon=True, name=f"matrix-run-{self.id}")
        self._sender.start()
        self._hb_stop = threading.Event()
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True, name="matrix-heartbeat")
        self._heartbeat.start()
        atexit.register(self._atexit)

    def __repr__(self) -> str:
        return f"Run(id={self.id!r}, name={self.name!r}, status={self.status!r})"

    # -- context manager
    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            self.finish("finished")
        elif isinstance(exc, KeyboardInterrupt):
            self.finish("killed", error="KeyboardInterrupt")
        else:
            self.finish("failed", error=_short_error(exc))
        return False

    # -- public logging API
    @property
    def step(self) -> int:
        """Next automatic step."""
        return self._step

    def log(self, metrics: Mapping[str, Any], step: int | None = None) -> None:
        """Logs several metrics at one step. `step=None` uses (and advances) the run's step counter.

        An explicit `step` does not move the counter.
        """
        if not isinstance(metrics, Mapping):
            raise TypeError("log() takes a mapping of metric name to number")
        points = []
        for key, value in metrics.items():
            self._check_key(key)
            points.append((key, to_metric_value(value, key)))
        if step is not None:
            step = self._check_step(step)
        ts = _now_iso()
        with self._cv:
            if step is None:
                step = self._step
                self._step += 1
            self._enqueue([{"key": k, "step": step, "value": v, "ts": ts} for k, v in points])

    def log_metric(self, key: str, value: Any, step: int | None = None) -> None:
        self.log({key: value}, step=step)

    def log_metrics(self, metrics: Mapping[str, Any], step: int | None = None) -> None:
        self.log(metrics, step=step)

    def _log_system(self, metrics: Mapping[str, Any], step: int) -> None:
        """Telemetry path: `sys/` keys allowed, explicit step (seconds since start)."""
        ts = _now_iso()
        points = []
        for key, value in metrics.items():
            try:
                points.append({"key": key, "step": int(step), "value": to_metric_value(value, key), "ts": ts})
            except TypeError:
                continue
        with self._cv:
            self._enqueue(points)

    def log_params(self, params: Mapping[str, Any]) -> None:
        """Params are immutable server-side; re-sending a different value logs a warning (409) and goes on."""
        clean = jsonable(_config_dict(params))
        with self._cv:
            self._params.update(clean)
            self._cv.notify_all()

    def log_report(self, kind: str, name: str, data: Any) -> None:
        if kind not in REPORT_KINDS:
            raise ValueError(f"report kind must be one of {REPORT_KINDS}, got {kind!r}")
        body = {"kind": kind, "name": str(name), "data": jsonable(data)}
        size = len(json.dumps(body, separators=(",", ":")))
        if size > MAX_REPORT_BYTES:
            raise ValueError(f"report {name!r} is {size} bytes; the limit is {MAX_REPORT_BYTES} "
                             "(log large tables with log_artifact)")
        self._submit("POST", f"/api/v1/runs/{_seg(self.id)}/reports", body)

    def log_table(self, name: str, columns: Iterable[Any], rows: Iterable[Iterable[Any]]) -> None:
        """TableReport: at most 1000 rows, long text cut at 2000 chars, `truncated` says if anything was cut."""
        cols = [str(c) for c in columns]
        truncated = False
        out: list[list[Any]] = []
        for i, row in enumerate(rows):
            if i >= MAX_TABLE_ROWS:
                truncated = True
                break
            cells = []
            for cell in row:
                cell = jsonable(cell)
                if isinstance(cell, str) and len(cell) > MAX_CELL_CHARS:
                    cell, truncated = cell[:MAX_CELL_CHARS], True
                elif isinstance(cell, (dict, list)):
                    text = json.dumps(cell, ensure_ascii=False)
                    if len(text) > MAX_CELL_CHARS:
                        cell, truncated = text[:MAX_CELL_CHARS], True
                cells.append(cell)
            out.append(cells)
        self.log_report("table", name, {"columns": cols, "rows": out, "truncated": truncated})

    def log_artifact(self, path: str | os.PathLike, name: str | None = None, kind: str = "file",
                     retries: int = 3) -> dict[str, Any] | None:
        """Uploads a file (a directory is zipped) and links it to the run. Synchronous.

        Missing paths raise; network failures are retried, then logged and `None` is returned.
        """
        if kind not in ARTIFACT_KINDS:
            raise ValueError(f"artifact kind must be one of {ARTIFACT_KINDS}, got {kind!r}")
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"artifact path does not exist: {src}")
        tmpdir = None
        try:
            if src.is_dir():
                tmpdir = tempfile.mkdtemp(prefix="matrix-artifact-")
                src = Path(shutil.make_archive(os.path.join(tmpdir, src.name or "artifact"), "zip", src))
            filename = src.name
            link_name = name or filename
            sha = hashlib.sha256()
            with src.open("rb") as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b""):
                    sha.update(chunk)
            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            headers = {"X-Filename": filename, "X-Sha256": sha.hexdigest(), "Content-Type": media_type,
                       "X-Owner-Kind": "run", "X-Owner-Id": self.id, "Content-Length": str(src.stat().st_size)}

            def chunks() -> Iterator[bytes]:
                with src.open("rb") as fh:
                    yield from iter(lambda: fh.read(CHUNK), b"")

            timeout = httpx.Timeout(30.0, read=600.0, write=600.0)
            for attempt in range(retries):
                try:
                    resp = self._http.post("/api/v1/artifacts", content=chunks(), headers=headers, timeout=timeout)
                    if resp.status_code < 500 and resp.status_code != 429:
                        resp.raise_for_status()
                        artifact = resp.json()
                        link = self._http.post(f"/api/v1/runs/{_seg(self.id)}/artifacts",
                                               json={"artifact_id": artifact["id"], "name": link_name,
                                                     "kind": kind})
                        link.raise_for_status()
                        return artifact
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code < 500 and exc.response.status_code != 429:
                        logger.error("artifact %s rejected: HTTP %s %s", filename, exc.response.status_code,
                                  exc.response.text[:200])
                        return None
                except httpx.HTTPError as exc:
                    logger.warning("artifact %s upload failed (attempt %d): %s", filename, attempt + 1, exc)
                if attempt + 1 < retries:
                    time.sleep(self._backoff(attempt))
            logger.error("artifact %s not uploaded after %d attempts", filename, retries)
            return None
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    # -- lifecycle
    def flush(self, timeout: float | None = None) -> bool:
        """Waits until everything queued so far was sent (or dropped). True when the queue drained."""
        end = time.monotonic() + (self.flush_timeout if timeout is None else timeout)
        with self._cv:
            self._cv.notify_all()
            while self._points or self._params or self._ops or self._busy:
                left = end - time.monotonic()
                if left <= 0 or not self._sender.is_alive():
                    return False
                self._cv.wait(min(left, 0.05))
        return True

    def finish(self, status: str = "finished", error: str | None = None, timeout: float | None = None) -> None:
        """Flushes (up to `timeout`, default 30 s) and closes the run. Idempotent; never raises on network errors."""
        if status not in RUN_STATUSES:
            raise ValueError(f"status must be one of {RUN_STATUSES}, got {status!r}")
        with self._finish_lock:
            if self.status != "running":
                return
            self.status = status
        with contextlib.suppress(Exception):
            atexit.unregister(self._atexit)
        if self._monitor is not None:
            self._monitor.stop()
        self._hb_stop.set()
        deadline = time.monotonic() + (self.flush_timeout if timeout is None else timeout)
        with self._cv:
            self._closing = True
            self._deadline = deadline
            self._cv.notify_all()
        self._wake.set()
        self._sender.join(max(0.0, deadline - time.monotonic()) + 0.5)
        with self._cv:
            left = len(self._points)
            if left:
                self.dropped_points += left
                self._points.clear()
        if left:
            logger.warning("run %s: %d metric points not sent before the flush timeout", self.id, left)
        body: dict[str, Any] = {"status": status}
        if error:
            body["error"] = error[:MAX_ERROR_CHARS]
        self._call("PATCH", f"/api/v1/runs/{_seg(self.id)}", body,
                   deadline=max(deadline, time.monotonic() + 2.0))
        global _current
        with _current_lock:
            if _current is self:
                _current = None
        if self._owns_client:
            self._http.close()

    def _atexit(self) -> None:
        exc = getattr(sys, "last_exc", None) or getattr(sys, "last_value", None)
        if exc is None:
            self.finish("finished")
        elif isinstance(exc, KeyboardInterrupt):
            self.finish("killed", error="KeyboardInterrupt")
        else:
            self.finish("failed", error=_short_error(exc))

    # -- internals
    @staticmethod
    def _check_key(key: Any) -> None:
        if not isinstance(key, str) or not METRIC_KEY.match(key):
            raise ValueError(f"invalid metric key {key!r}: 1-200 chars of letters, digits and _ . / : @ - space")
        if key.startswith("sys/"):
            raise ValueError(f"metric key {key!r}: the sys/ prefix is reserved for system telemetry")

    @staticmethod
    def _check_step(step: Any) -> int:
        if isinstance(step, bool) or not isinstance(step, numbers.Integral):
            if hasattr(step, "item"):
                step = step.item()
            if isinstance(step, bool) or not isinstance(step, numbers.Integral):
                raise TypeError(f"step must be an int, got {type(step).__name__}")
        step = int(step)
        if step < 0:
            raise ValueError("step must be >= 0")
        return step

    def _enqueue(self, points: list[dict[str, Any]]) -> None:
        # caller holds self._cv
        if self._closing:
            self.dropped_points += len(points)
            self._warn_once("closed", "run %s: logged after finish(), points dropped", self.id)
            return
        if len(points) > self._max_queue:
            self.dropped_points += len(points) - self._max_queue
            points = points[-self._max_queue:]
        overflow = len(self._points) + len(points) - self._max_queue
        if overflow > 0:
            for _ in range(overflow):
                self._points.popleft()
            self.dropped_points += overflow
            self._warn_once("overflow", "run %s: metric queue full, dropping the oldest points", self.id)
        self._points.extend(points)
        if len(self._points) >= self._batch_size:
            self._cv.notify_all()

    def _submit(self, method: str, path: str, body: Any) -> None:
        with self._cv:
            self._ops.append((method, path, body))
            self._cv.notify_all()

    def _warn_once(self, tag: str, msg: str, *args: Any, limit: int = 3) -> None:
        n = self._warned.get(tag, 0)
        if n < limit:
            self._warned[tag] = n + 1
            logger.warning(msg, *args)

    def _backoff(self, attempt: int) -> float:
        return min(self._backoff_max, self._backoff_base * 2 ** attempt) * random.uniform(0.5, 1.0)

    def _call(self, method: str, path: str, body: Any, *, deadline: float | None = None) -> httpx.Response | None:
        """One request with retries on network errors, 429 and 5xx. Never raises.

        Returns the final response (2xx or a non-retryable 4xx), or None when it gave up.
        """
        attempt = 0
        while True:
            try:
                resp = self._http.request(method, path, json=body)
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp
                reason = f"HTTP {resp.status_code}"
            except httpx.HTTPError as exc:
                reason = f"{type(exc).__name__}: {exc}"
            except Exception as exc:  # noqa: BLE001 - never let the sender die
                reason = f"{type(exc).__name__}: {exc}"
            delay = self._backoff(attempt)
            attempt += 1
            dl = deadline if deadline is not None else self._deadline
            if dl is not None and time.monotonic() + delay > dl:
                self._warn_once(f"giveup:{path}", "run %s: giving up on %s %s (%s)", self.id, method, path, reason)
                return None
            self._warn_once(f"retry:{path}", "run %s: %s %s failed (%s), retrying", self.id, method, path, reason)
            if self._wake.is_set():
                time.sleep(delay)  # finishing: retries are bounded by the deadline checked above
            else:
                self._wake.wait(delay)  # finish() interrupts the sleep

    def _send_loop(self) -> None:
        while True:
            with self._cv:
                if not (self._closing or self._params or self._ops or len(self._points) >= self._batch_size):
                    self._cv.wait(self._flush_interval)
                if self._closing and self._deadline is not None and time.monotonic() > self._deadline:
                    return
                params, self._params = self._params, {}
                ops = list(self._ops)
                self._ops.clear()
                n = min(len(self._points), self._batch_size)
                batch = [self._points.popleft() for _ in range(n)]
                if not (params or ops or batch):
                    if self._closing:
                        return
                    continue
                self._busy = True
            try:
                if params:
                    self._send_params(params)
                for method, path, body in ops:
                    resp = self._call(method, path, body)
                    if resp is not None and resp.status_code >= 400:
                        self._warn_once(f"op:{path}", "run %s: %s %s rejected: HTTP %s %s", self.id, method,
                                        path, resp.status_code, resp.text[:200])
                if batch:
                    self._send_points(batch)
            except Exception:
                logger.exception("run %s: sender error", self.id)
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()

    def _send_params(self, params: dict[str, Any]) -> None:
        resp = self._call("POST", f"/api/v1/runs/{_seg(self.id)}/params", {"params": params})
        if resp is None:
            return
        if resp.status_code == 409:
            try:
                conflicts = resp.json().get("conflicts")
            except ValueError:
                conflicts = None
            logger.warning("run %s: params already set with different values, kept the originals: %s",
                        self.id, conflicts)
        elif resp.status_code >= 400:
            logger.warning("run %s: params rejected: HTTP %s %s", self.id, resp.status_code, resp.text[:200])

    def _send_points(self, batch: list[dict[str, Any]]) -> None:
        resp = self._call("POST", f"/api/v1/runs/{_seg(self.id)}/metrics", {"points": batch})
        if resp is None:
            with self._cv:
                self.dropped_points += len(batch)
            return
        if resp.status_code >= 400:
            with self._cv:
                self.dropped_points += len(batch)
            self._warn_once("points", "run %s: %d points rejected: HTTP %s %s", self.id, len(batch),
                            resp.status_code, resp.text[:200])
            return
        try:
            rejected = (resp.json() or {}).get("rejected") or []
        except ValueError:
            rejected = []
        if rejected:
            self._warn_once("rejected", "run %s: server rejected %d points, e.g. %s", self.id, len(rejected),
                            rejected[0])

    def _heartbeat_loop(self) -> None:
        while not self._hb_stop.wait(self._heartbeat_interval):
            try:
                self._http.post(f"/api/v1/runs/{_seg(self.id)}/heartbeat", timeout=5.0)
            except Exception as exc:  # noqa: BLE001 - next beat retries
                logger.debug("heartbeat failed: %s", exc)


# -- entry points ---------------------------------------------------------------------------------


def _resolve_client(client: MatrixClient | None) -> tuple[MatrixClient, bool]:
    from . import client as client_mod

    if client is not None:
        return client, False
    if client_mod._default_client is not None:
        return client_mod._default_client, False
    key = os.environ.get("MATRIX_API_KEY")
    if not key:
        raise MatrixError("MATRIX API key missing: call matrix_sdk.init(), pass client=, or set MATRIX_API_KEY")
    endpoint = os.environ.get("MATRIX_ENDPOINT") or client_mod.DEFAULT_ENDPOINT
    return client_mod.MatrixClient(key, endpoint), True


def start_run(
    name: str | None = None,
    group: str | None = None,
    tags: Iterable[str] = (),
    config: Any = None,
    system_metrics: bool = True,
    system_interval: float = 10.0,
    capture_git: bool = True,
    capture_env: bool = True,
    dataset_version: str | None = None,
    *,
    sweep_id: str | None = None,
    parent_run_id: str | None = None,
    client: MatrixClient | None = None,
    flush_timeout: float = 30.0,
    system_backends: list[Any] | None = None,
    **run_options: Any,
) -> Run:
    """Creates the run (`POST /api/v1/runs`, synchronous) and starts its sender, heartbeat and telemetry."""
    http_client, owns = _resolve_client(client)
    body: dict[str, Any] = {"tags": [str(t) for t in tags]}
    if name is not None:
        body["name"] = name
    if group is not None:
        body["group"] = group
    params = jsonable(_config_dict(config))
    if params:
        body["params"] = params
    if capture_git:
        git = _capture_git_safe()
        if git is not None:
            body["git"] = git
    if capture_env:
        body["env"] = capture_env_safe()
    if dataset_version is not None:
        body["dataset_version_id"] = dataset_version
    if sweep_id is not None:
        body["sweep_id"] = sweep_id
    if parent_run_id is not None:
        body["parent_run_id"] = parent_run_id
    try:
        resp = http_client._http.post("/api/v1/runs", json=body)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MatrixError(f"could not create run: HTTP {exc.response.status_code} {exc.response.text[:200]}") \
            from None
    except httpx.HTTPError as exc:
        raise MatrixError(f"could not create run: {exc}") from None
    run = Run(http_client, resp.json(), owns_client=owns, flush_timeout=flush_timeout, **run_options)
    if system_metrics:
        from .system import SystemMonitor

        run._monitor = SystemMonitor(run._log_system, interval=system_interval, backends=system_backends)
        run._monitor.start()
    global _current
    with _current_lock:
        _current = run
    return run


def _capture_git_safe() -> dict[str, Any] | None:
    try:
        return capture_git()
    except Exception:  # noqa: BLE001
        return None


def capture_env_safe() -> dict[str, Any]:
    try:
        return capture_env()
    except Exception:  # noqa: BLE001
        return {"python": platform.python_version(), "platform": None, "hostname": None, "packages": []}


def current_run() -> Run | None:
    return _current


def _require_run() -> Run:
    run = _current
    if run is None:
        raise MatrixError("no active run: call matrix_sdk.start_run() first")
    return run


def log(metrics: Mapping[str, Any], step: int | None = None) -> None:
    _require_run().log(metrics, step=step)


def log_params(params: Mapping[str, Any]) -> None:
    _require_run().log_params(params)


def finish(status: str = "finished", error: str | None = None) -> None:
    run = _current
    if run is not None:
        run.finish(status, error=error)


def resolve_run(run: Run | None) -> Run | None:
    """The given run, else the current one (integrations)."""
    return run if run is not None else _current


_KEY_BAD = re.compile(r"[^A-Za-z0-9_./:@ -]")


def safe_key(key: str) -> str:
    """Maps an arbitrary library key onto the metric-key alphabet (integrations never crash on a name)."""
    key = _KEY_BAD.sub("_", str(key))[:200] or "_"
    return "_" + key[1:] if key.startswith("sys/") else key

