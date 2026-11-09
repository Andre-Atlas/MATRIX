"""Request body size limit for every route (ASGI middleware).

`/api/v1/*` (JSON API) is bounded by `settings.max_json_body_bytes`; everything else (OTLP ingest)
by `settings.max_body_bytes`, which ingest also enforces itself on the (decompressed) body.
A declared `Content-Length` over the limit is refused before the app runs; a chunked or
undeclared body is counted as it streams and refused once it crosses the limit. Both give 413.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.exceptions import HTTPException

JSON_API_PREFIX = "/api/v1/"


class BodyTooLarge(HTTPException):
    """Raised from `receive` while the app reads the body. A Starlette HTTPException so FastAPI's
    body parsing re-raises it as is (instead of a 400) and the exception handler renders a 413."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="request body too large")


class BodyLimitMiddleware:
    def __init__(self, app: Any, max_body_bytes: int, max_json_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_json_body_bytes = max_json_body_bytes

    def _limit(self, path: str) -> int:
        if path.startswith(JSON_API_PREFIX) or path == JSON_API_PREFIX.rstrip("/"):
            return min(self.max_json_body_bytes, self.max_body_bytes)
        return self.max_body_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope.get("path", ""))
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _send_error(send, 400, "invalid content-length")
                    return
                if declared > limit:
                    await _send_error(send, 413, "request body too large")
                    return

        received = 0
        started = False

        async def limited_receive() -> dict:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        async def tracking_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            # Raised outside a route's exception handling (e.g. while a middleware read the body).
            if started:
                raise
            await _send_error(send, 413, "request body too large")


async def _send_error(send: Any, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
