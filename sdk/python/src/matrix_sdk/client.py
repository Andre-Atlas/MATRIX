"""MatrixClient: init(), scores, prompts (with SWR cache) and datasets."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

from . import tracing
from .prompts import Prompt

DEFAULT_ENDPOINT = "http://localhost:8000"

log = logging.getLogger("matrix_sdk")
_clock = time.monotonic  # monkeypatched in tests


class MatrixError(RuntimeError):
    pass


def _seg(value: Any) -> str:
    """One URL path segment: `/`, `?`, `#`, `%` and spaces in ids or names stay inside it."""
    return quote(str(value), safe="")


def _clean_no_proxy() -> None:
    for var in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(var)
        if val and "[::1]" in val:
            os.environ[var] = ",".join(p.strip() for p in val.split(",") if p.strip() != "[::1]")


class MatrixClient:
    def __init__(
        self,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        project_name: str | None = None,
        tracer_provider: TracerProvider | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
        *,
        owns_provider: bool = True,
        span_processors: list[SpanProcessor] | None = None,
    ) -> None:
        _clean_no_proxy()
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.project_name = project_name
        self.tracer_provider = tracer_provider
        # When init() attached to an application's provider, only MATRIX's processors are ours.
        self._owns_provider = owns_provider
        self._span_processors = list(span_processors or [])
        self._http = httpx.Client(
            base_url=self.endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=timeout,
        )
        self._cache: dict[tuple, tuple[Prompt, float]] = {}
        self._refreshing: set[tuple] = set()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    # -- lifecycle
    def shutdown(self) -> None:
        if self.tracer_provider is not None:
            if self._owns_provider:
                self.tracer_provider.force_flush()
                self.tracer_provider.shutdown()
            else:
                for processor in self._span_processors:
                    processor.force_flush()
                    processor.shutdown()
        self._http.close()

    def flush(self) -> None:
        if self.tracer_provider is not None:
            self.tracer_provider.force_flush()

    # -- http
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    # -- scores
    def score(
        self,
        trace_id: str,
        name: str,
        value: float,
        span_id: str | None = None,
        comment: str | None = None,
        source: str = "sdk",
        label: str | None = None,
    ) -> dict:
        body = {"trace_id": trace_id, "name": name, "value": value, "source": source}
        if span_id is not None:
            body["span_id"] = span_id
        if label is not None:
            body["label"] = label
        if comment is not None:
            body["comment"] = comment
        return self._request("POST", "/api/v1/scores", json=body)

    # -- prompts
    def _fetch_prompt(self, name: str, label: str, version: int | None) -> Prompt:
        params = {"version": version} if version is not None else {"label": label}
        return Prompt.from_api(self._request("GET", f"/api/v1/prompts/{_seg(name)}", params=params))

    def get_prompt(self, name: str, label: str = "production", version: int | None = None,
                   cache_ttl: float = 60) -> Prompt:
        key = (name, label, version)
        with self._lock:
            hit = self._cache.get(key)
        if hit is None:
            prompt = self._fetch_prompt(name, label, version)
            with self._lock:
                self._cache[key] = (prompt, _clock())
            return prompt
        prompt, fetched_at = hit
        if _clock() - fetched_at >= cache_ttl:
            self._refresh_async(key)
        return prompt

    def _refresh_async(self, key: tuple) -> None:
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def run() -> None:
            try:
                prompt = self._fetch_prompt(*key)
                with self._lock:
                    self._cache[key] = (prompt, _clock())
            except httpx.HTTPError as exc:  # keep serving the stale value; retried on next stale read
                log.warning("prompt refresh failed for %s: %s", key, exc)
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        t = threading.Thread(target=run, daemon=True, name="matrix-prompt-refresh")
        with self._lock:
            # Only live refreshes are kept, so a long-running process does not accumulate threads.
            self._threads = [x for x in self._threads if x.is_alive()]
            self._threads.append(t)
        t.start()

    def wait_for_refreshes(self, timeout: float = 5.0) -> None:
        with self._lock:
            threads = list(self._threads)
        for t in threads:
            t.join(timeout)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]

    # -- datasets
    def create_dataset_item(
        self,
        dataset_id: str,
        input: dict | Any,
        expected_output: str | None = None,
        context: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        body: dict[str, Any] = {"input": input}
        if expected_output is not None:
            body["expected_output"] = expected_output
        if context is not None:
            body["context"] = context
        if metadata is not None:
            body["metadata"] = metadata
        return self._request("POST", f"/api/v1/datasets/{_seg(dataset_id)}/items", json=body)

    @staticmethod
    def current_trace_id() -> str | None:
        return tracing.current_trace_id()


def init(
    api_key: str | None = None,
    endpoint: str | None = None,
    project_name: str | None = None,
    service_name: str = "app",
    batch: bool = True,
    set_global: bool = True,
) -> MatrixClient:
    key = api_key or os.environ.get("MATRIX_API_KEY")
    if not key:
        raise MatrixError("MATRIX API key missing: pass api_key= or set MATRIX_API_KEY")
    base = (endpoint or os.environ.get("MATRIX_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")

    exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces", headers={"Authorization": f"Bearer {key}"})
    processors: list[SpanProcessor] = [
        tracing.SessionSpanProcessor(),
        BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter),
    ]
    existing = trace.get_tracer_provider() if set_global else None
    if isinstance(existing, TracerProvider):
        # The app already set an SDK provider globally (OTel allows only one): export its spans
        # to MATRIX too instead of creating a second provider that would never become global.
        provider, owns = existing, False
    else:
        attrs = {"service.name": service_name}
        if project_name:
            attrs["matrix.project_name"] = project_name
        provider, owns = TracerProvider(resource=Resource.create(attrs)), True
    for processor in processors:
        provider.add_span_processor(processor)
    if set_global and owns:
        trace.set_tracer_provider(provider)
    tracing._set_provider(provider)  # helpers and instrumentations share one provider
    return MatrixClient(
        key, base, project_name, tracer_provider=provider, owns_provider=owns, span_processors=processors
    )
