"""Operator-trusted local model endpoints (ADR-016, exception to ADR-012).

The egress policy blocks private and loopback addresses, but the local model server (Ollama, or
llama.cpp/vLLM with an OpenAI-compatible API) lives on `127.0.0.1` or on the compose network. The
operator lists those servers in `MATRIX_LOCAL_MODEL_ENDPOINTS`; `local_model_client(...)` may then
reach them even when they resolve to a private address, under these rules:

- only for the purposes in `LOCAL_MODEL_PURPOSES` (`model`, `embedding`);
- scheme, host and port must match a listed endpoint exactly (any path);
- every hop of a redirect is checked against the same list (anything else is refused);
- link-local, metadata, multicast and unspecified addresses stay refused even if listed;
- the list comes only from the operator's environment, never from request data, and is frozen
  when the client is built.

`egress_client(...)` (user URLs) never consults this list.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session

from matrix_server.egress.client import EgressClient
from matrix_server.egress.policy import (
    _DEFAULT_PORTS,
    _FORBIDDEN_URL_CHARS,
    FORBIDDEN,
    MAX_URL_CHARS,
    EgressError,
    EgressSettings,
    EgressTarget,
    _resolve,
    classify_ip,
    normalize_host,
)

logger = logging.getLogger("matrix_server.egress.local_models")

LOCAL_MODEL_PURPOSES = frozenset({"model", "embedding"})


class LocalModelSettings(BaseSettings):
    """Operator configuration for local model servers. Never derived from request data."""

    model_config = SettingsConfigDict(env_prefix="MATRIX_")

    # Comma-separated base URLs, e.g. "http://ollama:11434,http://127.0.0.1:11434". Empty = none.
    local_model_endpoints: str = ""
    # Base URL the engines use; honoured only if it is one of `local_model_endpoints`.
    local_llm_url: str = "http://127.0.0.1:11434"
    local_chat_model: str = "qwen2.5:0.5b"
    local_embed_model: str = "all-minilm"
    local_vision_model: str = "moondream"


@dataclass(frozen=True)
class LocalEndpoint:
    scheme: str
    host: str  # normalized: lowercase hostname, "localhost", or IP without brackets
    port: int

    @property
    def base_url(self) -> str:
        netloc = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{netloc}:{self.port}"


def _normalize_local_host(raw: str) -> str:
    host = raw.strip().lower().removesuffix(".")
    if host == "localhost":
        return host
    norm = normalize_host(raw)
    return str(norm)


def _split(url: str) -> tuple[str, str, int, Any]:
    """(scheme, normalized host, port, SplitResult) of an http(s) URL without credentials."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_CHARS:
        raise EgressError("invalid_url", "invalid or too long URL")
    if _FORBIDDEN_URL_CHARS.search(url):
        raise EgressError("invalid_url", "URL contains forbidden characters")
    try:
        parts = urlsplit(url)
        port = parts.port
        raw_host = parts.hostname
    except ValueError as exc:
        raise EgressError("invalid_url", "invalid URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise EgressError("scheme_not_allowed", "only http and https are allowed")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise EgressError("userinfo_not_allowed", "credentials in the URL are not allowed")
    if not raw_host:
        raise EgressError("invalid_host", "missing host")
    host = _normalize_local_host(raw_host)
    return scheme, host, port if port is not None else _DEFAULT_PORTS[scheme], parts


def parse_local_endpoints(raw: str) -> tuple[LocalEndpoint, ...]:
    """Parse `MATRIX_LOCAL_MODEL_ENDPOINTS`. Invalid entries are skipped with a warning (fail closed)."""
    out: list[LocalEndpoint] = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            scheme, host, port, _ = _split(entry)
        except EgressError as exc:
            logger.warning("ignoring invalid MATRIX_LOCAL_MODEL_ENDPOINTS entry (%s)", exc.reason)
            continue
        ep = LocalEndpoint(scheme, host, port)
        if ep not in out:
            out.append(ep)
    return tuple(out)


def check_local_url(url: str, *, endpoints: tuple[LocalEndpoint, ...], purpose: str) -> EgressTarget:
    """Validate `url` against the operator's local endpoints and pin it to one resolved IP."""
    if purpose not in LOCAL_MODEL_PURPOSES:
        raise EgressError("purpose_not_allowed", "local model endpoints serve only model/embedding")
    scheme, host, port, parts = _split(url)
    if LocalEndpoint(scheme, host, port) not in endpoints:
        raise EgressError("local_endpoint_not_allowed", f"host {host} is not a local model endpoint", host=host)
    try:
        ips = [str(ipaddress.ip_address(host))]
    except ValueError:
        ips = _resolve(host, port)
    for ip in ips:
        try:
            verdict = classify_ip(ip)
        except ValueError:
            verdict = FORBIDDEN
        if verdict == FORBIDDEN:  # link-local/metadata/multicast stay refused even when listed
            raise EgressError(verdict, f"host {host} resolves to a blocked address", host=host)
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if parts.port is None else f"{netloc_host}:{port}"
    normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    try:
        parsed = httpx.URL(normalized)
    except httpx.InvalidURL as exc:
        raise EgressError("invalid_url", "invalid URL", host=host) from exc
    if parsed.raw_host.decode("ascii").lower() != host or (parsed.port or _DEFAULT_PORTS[scheme]) != port:
        raise EgressError("invalid_url", "ambiguous URL", host=host)
    if parsed.userinfo:
        raise EgressError("userinfo_not_allowed", "credentials in the URL are not allowed", host=host)
    return EgressTarget(url=normalized, host=host, ip=ips[0], port=port)


class LocalModelClient(EgressClient):
    """`EgressClient` whose every hop is checked by `check_local_url` against a frozen endpoint list.

    Same pinning, caps, redirect handling and audit rows as the user-URL client (`trust_env=False`,
    no proxy). Without a `session_factory` (health probes) attempts are logged instead of audited.
    """

    def __init__(
        self,
        *,
        project_id: str | None,
        session_factory: Callable[[], Session] | None,
        purpose: str,
        timeout: float,
        max_response_bytes: int,
        max_redirects: int,
        settings: LocalModelSettings,
    ) -> None:
        if purpose not in LOCAL_MODEL_PURPOSES:
            raise ValueError(f"purpose must be one of {sorted(LOCAL_MODEL_PURPOSES)}")
        super().__init__(
            project_id=project_id or "",
            session_factory=session_factory,  # type: ignore[arg-type]
            purpose=purpose,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_redirects=max_redirects,
            # Ports are governed by the endpoint list; the request-size cap is shared with ADR-012.
            settings=EgressSettings(),
        )
        self.project_id = project_id  # type: ignore[assignment]
        self._endpoints: tuple[LocalEndpoint, ...] = parse_local_endpoints(settings.local_model_endpoints)

    @property
    def endpoints(self) -> tuple[LocalEndpoint, ...]:
        return self._endpoints

    def _check(self, url: str) -> EgressTarget:
        return check_local_url(url, endpoints=self._endpoints, purpose=self.purpose)

    def _audit(self, host: str | None, **detail: Any) -> None:
        if self.session_factory is None:
            logger.info("egress.request local host=%s purpose=%s %s", host, self.purpose, detail)
            return
        super()._audit(host, **{"local": True, **detail})


def get_local_model_settings() -> LocalModelSettings:
    """Fresh read of the operator's environment (not cached, so tests and reloads see changes)."""
    return LocalModelSettings()


def local_model_client(
    *,
    purpose: str,
    project_id: str | None = None,
    session_factory: Callable[[], Session] | None = None,
    timeout: float = 120.0,
    max_response_bytes: int = 20_000_000,
    max_redirects: int = 3,
    settings: LocalModelSettings | None = None,
) -> LocalModelClient:
    """Client for the operator's local model servers (`purpose` must be `model` or `embedding`)."""
    return LocalModelClient(
        project_id=project_id,
        session_factory=session_factory,
        purpose=purpose,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        max_redirects=max_redirects,
        settings=settings or get_local_model_settings(),
    )


def resolve_local_model_base_url(settings: LocalModelSettings | None = None) -> str | None:
    """`MATRIX_LOCAL_LLM_URL` normalized (no trailing slash) if it is a listed endpoint, else None."""
    settings = settings or get_local_model_settings()
    endpoints = parse_local_endpoints(settings.local_model_endpoints)
    try:
        scheme, host, port, parts = _split(settings.local_llm_url.strip())
    except EgressError:
        return None
    ep = LocalEndpoint(scheme, host, port)
    if ep not in endpoints:
        return None
    return ep.base_url + parts.path.rstrip("/")


def local_llm_available(timeout: float = 2.0, settings: LocalModelSettings | None = None) -> bool:
    """True if the configured local model server answers `GET /api/tags` (Ollama) or `/v1/models`."""
    settings = settings or get_local_model_settings()
    base = resolve_local_model_base_url(settings)
    if base is None:
        return False
    client = local_model_client(purpose="model", timeout=timeout, max_response_bytes=5_000_000, settings=settings)
    for path in ("/api/tags", "/v1/models"):
        try:
            if client.get(base + path).is_success:
                return True
        except EgressError:
            continue
    return False


__all__ = [
    "LOCAL_MODEL_PURPOSES",
    "LocalEndpoint",
    "LocalModelClient",
    "LocalModelSettings",
    "check_local_url",
    "get_local_model_settings",
    "local_llm_available",
    "local_model_client",
    "parse_local_endpoints",
    "resolve_local_model_base_url",
]
