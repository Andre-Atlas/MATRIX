"""OTLP/HTTP trace receiver: `POST /v1/traces` (protobuf or JSON, optionally gzip)."""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from matrix_server.auth import current_project_id
from matrix_server.domain.spans import Span
from matrix_server.ingest.openinference import normalize_all
from matrix_server.ingest.otlp import OtlpDecodeError, decode_request, encode_response, is_supported

logger = logging.getLogger(__name__)

router = APIRouter()

# Called after each successful insert with (project_id, spans). Later tasks register rules and
# online evaluation here. A failing hook is logged (without payload) and never fails ingestion.
on_spans_ingested: list[Callable[[str, list[Span]], None]] = []


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail="request body too large")


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise _too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _gunzip(body: bytes, limit: int) -> bytes:
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        out = decompressor.decompress(body, limit + 1)
        if len(out) > limit or decompressor.unconsumed_tail:
            raise _too_large()
        out += decompressor.flush()
    except zlib.error as exc:
        raise HTTPException(status_code=400, detail="invalid gzip body") from exc
    if len(out) > limit:
        raise _too_large()
    return out


def run_hooks(project_id: str, spans: list[Span]) -> None:
    for hook in on_spans_ingested[:]:
        try:
            hook(project_id, spans)
        except Exception as exc:  # noqa: BLE001 - a hook must never break ingestion
            logger.error(
                "span ingestion hook %s failed: %s",
                getattr(hook, "__qualname__", repr(hook)),
                type(exc).__name__,
            )


@router.post("/v1/traces")
async def export_traces(request: Request, project_id: str = Depends(current_project_id)) -> Response:
    settings = request.app.state.settings
    content_type = request.headers.get("content-type", "")
    if not is_supported(content_type):
        raise HTTPException(status_code=415, detail="use application/x-protobuf or application/json")
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("identity", "", "gzip", "x-gzip"):
        raise HTTPException(status_code=415, detail="unsupported content encoding")

    limit = settings.max_body_bytes
    body = await _read_body(request, limit)
    if encoding in ("gzip", "x-gzip"):
        body = _gunzip(body, limit)

    def ingest() -> None:
        try:
            raw_spans = decode_request(body, content_type)
        except OtlpDecodeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        spans = normalize_all(raw_spans, project_id, settings.max_attribute_chars)
        if spans:
            request.app.state.span_store.insert(spans)
            run_hooks(project_id, spans)

    await run_in_threadpool(ingest)
    content, media_type = encode_response(content_type)
    return Response(content=content, media_type=media_type)
