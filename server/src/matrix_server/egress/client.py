"""`EgressClient`: the only way to call user-supplied URLs (ADR-012).

Each hop is validated by `check_url` (resolved once) and the TCP connection is pinned to the
validated IP while `Host`, SNI and certificate verification use the original hostname, so DNS
rebinding cannot redirect the request. Redirects are followed manually (each hop revalidated),
responses are streamed and capped (raw and decoded), and every attempt writes an audit row with
host, purpose and status or reason only.
"""

from __future__ import annotations

import json as jsonlib
import ssl
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
from sqlalchemy.orm import Session

from matrix_server import audit
from matrix_server.egress.policy import (
    EgressError,
    EgressSettings,
    EgressTarget,
    best_effort_host,
    check_url,
)

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_STRIP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "upgrade", "te",
     "trailer", "proxy-connection", "accept-encoding"}
)
_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "cookie2"})

# --- connection seam -----------------------------------------------------------------------------

Dialer = Callable[[str, int], tuple[str, int]]
_dialer: Dialer | None = None


def set_dialer_for_tests(fn: Dialer | None) -> None:
    """Map the validated `(ip, port)` to the address actually dialed (tests route a simulated public
    IP to a local server). The policy has already run on the validated IP. `None` restores."""
    global _dialer
    _dialer = fn


class _PinnedBackend(httpcore.SyncBackend):
    def __init__(self, host: str, ip: str) -> None:
        self._host = host
        self._ip = ip

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host.lower() != self._host:
            raise httpcore.ConnectError("unexpected host for pinned connection")
        ip, port = _dialer(self._ip, port) if _dialer else (self._ip, port)
        return super().connect_tcp(ip, port, timeout, local_address, socket_options)


class _PinnedTransport(httpx.HTTPTransport):
    """HTTPTransport whose pool dials the validated IP; TLS SNI/verification use the URL host."""

    def __init__(self, target: EgressTarget, ssl_context: ssl.SSLContext) -> None:
        super().__init__(verify=ssl_context, trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_PinnedBackend(target.host, target.ip),
        )


# --- response ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EgressResponse:
    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str  # final URL (internal use; never logged)

    @property
    def text(self) -> str:
        charset = httpx.Headers(self.headers).get("content-type", "")
        enc = "utf-8"
        for part in charset.split(";"):
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                enc = value.strip('"')
        try:
            return self.content.decode(enc, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return jsonlib.loads(self.content)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def _decode_body(raw: bytes, content_encoding: str, limit: int) -> bytes:
    codings = [c.strip().lower() for c in content_encoding.split(",") if c.strip()]
    data = raw
    for coding in reversed(codings):
        if coding == "identity":
            continue
        if coding in ("gzip", "x-gzip"):
            decoders = [zlib.decompressobj(16 + zlib.MAX_WBITS)]
        elif coding == "deflate":
            decoders = [zlib.decompressobj(zlib.MAX_WBITS), zlib.decompressobj(-zlib.MAX_WBITS)]
        else:
            raise EgressError("unsupported_encoding", "unsupported response content-encoding")
        for i, dec in enumerate(decoders):
            try:
                out = dec.decompress(data, limit + 1)
            except zlib.error as exc:
                if i + 1 < len(decoders):
                    continue
                raise EgressError("invalid_encoding", "invalid compressed response") from exc
            if len(out) > limit or dec.unconsumed_tail:
                raise EgressError("response_too_large", "decoded response exceeds the size limit")
            data = out
            break
    return data


# --- client --------------------------------------------------------------------------------------


class EgressClient:
    def __init__(
        self,
        *,
        project_id: str,
        session_factory: Callable[[], Session],
        purpose: str,
        timeout: float = 10.0,
        max_response_bytes: int = 1_000_000,
        max_redirects: int = 3,
        settings: EgressSettings | None = None,
    ) -> None:
        if not purpose or len(purpose) > 64:
            raise ValueError("purpose must be 1..64 characters")
        self.project_id = project_id
        self.session_factory = session_factory
        self.purpose = purpose
        self.timeout = float(timeout)
        self.max_response_bytes = int(max_response_bytes)
        self.max_redirects = max(0, min(int(max_redirects), 10))
        self.settings = settings or EgressSettings()
        self._ssl_context = httpx.create_ssl_context(trust_env=False)

    # audit ---------------------------------------------------------------------------------------

    def _audit(self, host: str | None, **detail: Any) -> None:
        session = self.session_factory()
        try:
            audit.record(
                session,
                action="egress.request",
                project_id=self.project_id,
                target=host,
                detail={"purpose": self.purpose, **detail},
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _check(self, url: str) -> EgressTarget:
        session = self.session_factory()
        try:
            return check_url(
                url, project_id=self.project_id, session=session, purpose=self.purpose, settings=self.settings
            )
        finally:
            session.close()

    # request -------------------------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        json: Any = None,
    ) -> EgressResponse:
        method = method.upper()
        try:
            if method not in _ALLOWED_METHODS:
                raise EgressError("method_not_allowed", "HTTP method not allowed")
            if content is not None and json is not None:
                raise EgressError("invalid_request", "pass either content or json, not both")
            out_headers = httpx.Headers(
                {k: v for k, v in (headers or {}).items() if k.lower() not in _STRIP_REQUEST_HEADERS}
            )
            body = content
            if json is not None:
                body = jsonlib.dumps(json, separators=(",", ":")).encode("utf-8")
                out_headers.setdefault("content-type", "application/json")
            if body is not None and len(body) > self.settings.max_request_bytes:
                raise EgressError("request_too_large", "request body exceeds the size limit")
        except EgressError as exc:
            self._audit(best_effort_host(url), reason=exc.reason)
            raise
        out_headers["accept-encoding"] = "gzip, deflate"

        deadline = time.monotonic() + self.timeout
        current = url
        for hop in range(self.max_redirects + 1):
            try:
                target = self._check(current)
            except EgressError as exc:
                self._audit(exc.host or best_effort_host(current), reason=exc.reason)
                raise
            try:
                resp = self._send(target, method, out_headers, body, deadline)
            except EgressError as exc:
                self._audit(target.host, reason=exc.reason)
                raise
            self._audit(target.host, status=resp.status_code)
            location = resp.headers.get("location")
            if resp.status_code not in REDIRECT_STATUSES or not location:
                return resp
            if hop == self.max_redirects:
                self._audit(target.host, reason="too_many_redirects")
                raise EgressError("too_many_redirects", "too many redirects", host=target.host)
            nxt = urljoin(target.url, location.strip())
            if resp.status_code == 303 and method != "HEAD" or resp.status_code in (301, 302) and method == "POST":
                method, body = "GET", None
                for h in ("content-type", "content-length"):
                    out_headers.pop(h, None)
            if not _same_origin(target.url, nxt):
                for h in _CREDENTIAL_HEADERS:
                    out_headers.pop(h, None)
            current = nxt
        raise AssertionError("unreachable")  # pragma: no cover

    def get(self, url: str, **kw: Any) -> EgressResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> EgressResponse:
        return self.request("POST", url, **kw)

    def _send(
        self, target: EgressTarget, method: str, headers: httpx.Headers, body: bytes | None, deadline: float
    ) -> EgressResponse:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EgressError("timeout", "request timed out", host=target.host)
        timeout = httpx.Timeout(
            remaining, connect=min(self.settings.connect_timeout, remaining), pool=min(1.0, remaining)
        )
        limit = self.max_response_bytes
        transport = _PinnedTransport(target, self._ssl_context)
        try:
            with httpx.Client(
                transport=transport, trust_env=False, follow_redirects=False, timeout=timeout
            ) as client:
                req = client.build_request(method, target.url, headers=headers, content=body)
                resp = client.send(req, stream=True)
                try:
                    declared = resp.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > limit:
                        raise EgressError("response_too_large", "response exceeds the size limit")
                    raw = bytearray()
                    for chunk in resp.iter_raw():
                        raw += chunk
                        if len(raw) > limit:
                            raise EgressError("response_too_large", "response exceeds the size limit")
                        if time.monotonic() > deadline:
                            raise EgressError("timeout", "request timed out")
                    data = _decode_body(bytes(raw), resp.headers.get("content-encoding", ""), limit)
                    return EgressResponse(resp.status_code, resp.headers, data, str(target.url))
                finally:
                    resp.close()
        except EgressError as exc:
            exc.host = target.host
            raise
        except httpx.TimeoutException as exc:
            raise EgressError("timeout", "request timed out", host=target.host) from exc
        except (httpx.HTTPError, ssl.SSLError, OSError) as exc:
            raise EgressError("connection_error", "connection failed", host=target.host) from exc


def _same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlsplit(a), urlsplit(b)
        return (pa.scheme.lower(), (pa.hostname or "").lower(), pa.port) == (
            pb.scheme.lower(),
            (pb.hostname or "").lower(),
            pb.port,
        )
    except ValueError:
        return False


def egress_client(
    *,
    project_id: str,
    session_factory: Callable[[], Session],
    purpose: str,
    timeout: float = 10.0,
    max_response_bytes: int = 1_000_000,
    max_redirects: int = 3,
    settings: EgressSettings | None = None,
) -> EgressClient:
    return EgressClient(
        project_id=project_id,
        session_factory=session_factory,
        purpose=purpose,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        max_redirects=max_redirects,
        settings=settings,
    )
