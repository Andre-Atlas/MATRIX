"""OTLP/HTTP trace receiver: `POST /v1/traces` (protobuf or JSON, optionally gzip).

Status codes: 400 undecodable body, 413 body (or decompressed body, or normalized attribute
total of `max_normalized_factor` x `max_body_bytes`) too large, 415 unsupported media, 503
store failure (retryable). Invalid spans are dropped and reported in the OTLP `partial_success`
(`rejected_spans`, `error_message`) of a 200 response; the rest of the batch is stored.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from matrix_server.auth import current_project_id
from matrix_server.domain.spans import Span
from matrix_server.ingest.openinference import normalize_partial
from matrix_server.ingest.otlp import (
    AttributeBudgetExceeded,
    OtlpDecodeError,
    decode_batch,
    encode_response,
    is_supported,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Called after each successful insert with (project_id, spans). Later tasks register rules and
# online evaluation here. A failing hook is logged (without payload) and never fails ingestion.
on_spans_ingested: list[Callable[[str, list[Span]], None]] = []


def _too_large(detail: str = "request body too large") -> HTTPException:
    return HTTPException(status_code=413, detail=detail)


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
    """Decompress every gzip member (RFC 1952 allows several); the total is bounded by `limit`."""
    parts: list[bytes] = []
    size = 0
    data = body
    try:
        while True:
            remaining = limit - size
            decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            out = decompressor.decompress(data, remaining + 1)
            if len(out) > remaining or decompressor.unconsumed_tail:
                raise _too_large()
            out += decompressor.flush()
            if len(out) > remaining:
                raise _too_large()
            parts.append(out)
            size += len(out)
            if not decompressor.eof:
                raise HTTPException(status_code=400, detail="truncated gzip body")
            data = decompressor.unused_data
            if not data.strip(b"\x00"):  # trailing zero padding is tolerated, like gzip(1)
                break
    except zlib.error as exc:
        raise HTTPException(status_code=400, detail="invalid gzip body") from exc
    return b"".join(parts)


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

    max_attribute_bytes = settings.max_normalized_factor * limit

    def ingest() -> tuple[int, list[str]]:
        try:
            batch = decode_batch(body, content_type, max_attribute_bytes=max_attribute_bytes)
        except OtlpDecodeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AttributeBudgetExceeded as exc:
            raise _too_large("normalized span attributes too large") from exc
        spans, rejected, reasons = normalize_partial(batch.spans, project_id, settings.max_attribute_chars)
        if spans:
            try:
                request.app.state.span_store.insert(spans)
            except Exception as exc:  # surfaced as a retryable status; no payload logged
                logger.error("span store insert failed: %s", type(exc).__name__)
                raise HTTPException(
                    status_code=503, detail="span store unavailable", headers={"Retry-After": "5"}
                ) from exc
            run_hooks(project_id, spans)
        return batch.rejected + rejected, batch.errors + reasons

    rejected, errors = await run_in_threadpool(ingest)
    message = f"{rejected} span(s) rejected: {'; '.join(errors)}" if rejected else ""
    content, media_type = encode_response(content_type, rejected, message)
    return Response(content=content, media_type=media_type)
