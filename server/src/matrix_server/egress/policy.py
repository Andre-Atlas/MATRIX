"""Outbound URL policy (ADR-012): URL parsing, host normalization, IP classification, allowlist.

`check_url` is pure (reads the allowlist, resolves DNS once, writes nothing); the audit row for a
request attempt is written by `EgressClient`. Error messages never contain the URL, its query or
credentials: only a reason code and, at most, the normalized host.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
import idna
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from matrix_server.egress.models import EgressHost

MAX_URL_CHARS = 2048
MAX_HOST_CHARS = 253
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_LABEL_RE = re.compile(r"^(?:0x[0-9a-f]*|[0-9]+)$")
_FORBIDDEN_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\]")
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Multi-label public suffixes that must never be wildcarded. Single-label suffixes (`*.com`) are
# rejected structurally; this list covers the common registries without shipping the full PSL.
PUBLIC_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk", "net.uk",
        "com.br", "net.br", "org.br", "gov.br", "edu.br",
        "com.au", "net.au", "org.au", "edu.au", "gov.au",
        "co.jp", "ne.jp", "or.jp", "ac.jp", "co.nz", "org.nz", "co.za", "co.in", "co.kr",
        "com.ar", "com.mx", "com.cn", "com.tr", "com.pt", "com.es", "com.sg", "com.hk", "com.tw",
        "github.io", "gitlab.io", "herokuapp.com", "appspot.com", "vercel.app", "netlify.app",
        "pages.dev", "workers.dev", "web.app", "firebaseapp.com", "azurewebsites.net",
        "cloudfront.net", "amazonaws.com", "s3.amazonaws.com", "blogspot.com", "ngrok.io",
        "ngrok-free.app", "fly.dev", "onrender.com", "repl.co", "glitch.me",
    }
)


class EgressSettings(BaseSettings):
    """Operator configuration. Never derived from request data."""

    model_config = SettingsConfigDict(env_prefix="MATRIX_EGRESS_")

    mode: Literal["allowlist", "public"] = "allowlist"
    # Dev/test only: permits loopback/RFC 1918/ULA/CGNAT. Link-local (cloud metadata), multicast,
    # unspecified and reserved ranges stay blocked even then.
    allow_private: bool = False
    allowed_ports: str = "80,443"
    max_request_bytes: int = 1_000_000
    connect_timeout: float = 5.0

    def port_set(self) -> frozenset[int]:
        ports = set()
        for part in self.allowed_ports.split(","):
            part = part.strip()
            if part.isdigit() and 0 < int(part) < 65536:
                ports.add(int(part))
        return frozenset(ports)


class EgressError(Exception):
    """Outbound request refused or failed. `str(exc)` is safe to show and to log."""

    def __init__(self, reason: str, message: str | None = None, *, host: str | None = None) -> None:
        self.reason = reason
        self.host = host
        super().__init__(message or reason.replace("_", " "))


@dataclass(frozen=True)
class EgressTarget:
    url: str  # normalized URL (ASCII host, no userinfo, no fragment)
    host: str  # normalized ASCII host (IPv6 without brackets)
    ip: str  # validated address the connection must use
    port: int


# --- resolver (test seam) ------------------------------------------------------------------------

Resolver = Callable[[str, int], Sequence[str]]


def _system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


_resolver: Resolver = _system_resolver


def set_resolver_for_tests(fn: Resolver | None) -> None:
    """Replace DNS resolution: `fn(host, port) -> [ip, ...]`. `None` restores the system resolver."""
    global _resolver
    _resolver = fn or _system_resolver


def _resolve(host: str, port: int) -> list[str]:
    try:
        raw = list(_resolver(host, port))
    except (OSError, UnicodeError) as exc:
        raise EgressError("dns_failure", f"could not resolve {host}", host=host) from exc
    seen: list[str] = []
    for ip in raw:
        ip = str(ip).split("%", 1)[0]  # drop IPv6 zone id
        if ip not in seen:
            seen.append(ip)
    if not seen:
        raise EgressError("dns_failure", f"could not resolve {host}", host=host)
    return seen


# --- IP classification ---------------------------------------------------------------------------

_V4_ALWAYS = tuple(
    ipaddress.ip_network(n) for n in ("0.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4")
)
_V4_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "172.16.0.0/12", "192.0.0.0/24",
        "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    )
)
_V6_ALWAYS = tuple(ipaddress.ip_network(n) for n in ("::/128", "fe80::/10", "fec0::/10", "ff00::/8"))
_V6_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in ("::1/128", "fc00::/7", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001:db8::/32")
)
_V4_COMPAT = ipaddress.ip_network("::/96")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

FORBIDDEN = "forbidden_address"  # never allowed
PRIVATE = "private_address"  # allowed only with MATRIX_EGRESS_ALLOW_PRIVATE


def _classify_v4(ip: ipaddress.IPv4Address) -> str | None:
    if any(ip in n for n in _V4_ALWAYS) or ip.is_multicast or ip.is_link_local or ip.is_unspecified:
        return FORBIDDEN
    if ip.is_reserved:
        return FORBIDDEN
    if any(ip in n for n in _V4_BLOCKED) or ip.is_loopback or ip.is_private or not ip.is_global:
        return PRIVATE
    return None


def classify_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return None for a public address, else `forbidden_address` or `private_address`."""
    ip = ipaddress.ip_address(value) if isinstance(value, str) else value
    if isinstance(ip, ipaddress.IPv4Address):
        return _classify_v4(ip)
    if ip.scope_id:
        return FORBIDDEN
    if any(ip in n for n in _V6_ALWAYS) or ip.is_multicast or ip.is_link_local or ip.is_unspecified:
        return FORBIDDEN
    # IPv6 forms that carry an IPv4 address: the embedded address decides, and the wrapper ranges
    # (compatible, NAT64, 6to4, Teredo) are never treated as public on their own.
    embedded: list[ipaddress.IPv4Address] = []
    if ip.ipv4_mapped is not None:
        return _classify_v4(ip.ipv4_mapped)
    if ip in _V4_COMPAT or ip in _NAT64:
        embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if ip.sixtofour is not None:
        embedded.append(ip.sixtofour)
    if ip.teredo is not None:
        embedded.extend(ip.teredo)
    for v4 in embedded:
        if _classify_v4(v4) == FORBIDDEN:
            return FORBIDDEN
    if embedded:
        return PRIVATE
    if ip.is_reserved or ip.is_site_local:
        return FORBIDDEN
    if any(ip in n for n in _V6_BLOCKED) or ip.is_loopback or ip.is_private or not ip.is_global:
        return PRIVATE
    return None


def _ip_allowed(ip: str, settings: EgressSettings) -> str | None:
    """Reason code if the address is refused under the current settings, else None."""
    try:
        verdict = classify_ip(ip)
    except ValueError:
        return FORBIDDEN
    if verdict == PRIVATE and settings.allow_private:
        return None
    return verdict


# --- host normalization --------------------------------------------------------------------------


def _scripts(label: str) -> set[str]:
    out = set()
    for ch in label:
        if not ch.isalpha():
            continue
        try:
            script = unicodedata.name(ch).split(" ", 1)[0]
        except ValueError:
            script = "UNKNOWN"
        if script in {"CJK", "HIRAGANA", "KATAKANA", "HANGUL", "IDEOGRAPHIC"}:
            script = "CJK"
        out.add(script)
    return out


def _check_alabel(label: str) -> None:
    try:
        ulabel = idna.decode(label)
    except idna.IDNAError as exc:
        raise EgressError("invalid_host", "invalid IDNA hostname") from exc
    if ulabel.isascii():
        raise EgressError("invalid_host", "punycode label decodes to ASCII")
    scripts = _scripts(ulabel)
    if len(scripts) > 1 and not scripts <= {"LATIN", "CJK"}:
        raise EgressError("invalid_host", "mixed-script hostname")


def normalize_host(raw: str) -> str | ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Normalize a URL/allowlist host to lowercase ASCII (IDNA/UTS-46) or return an IP address.

    Rejects empty or oversized names, invalid labels, `localhost`, mixed-script or bogus punycode,
    and numeric/hex forms (`2130706433`, `127.1`, `0x7f.1`) that resolvers read as IP addresses.
    """
    host = raw.strip()
    if not host or len(host) > MAX_HOST_CHARS + 1:
        raise EgressError("invalid_host", "empty or too long hostname")
    if "%" in host:
        raise EgressError("invalid_host", "zone identifiers are not allowed")
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    if not host.isascii():
        try:
            host = idna.encode(host, uts46=True).decode("ascii")
        except (idna.IDNAError, UnicodeError) as exc:
            raise EgressError("invalid_host", "invalid IDNA hostname") from exc
    host = host.lower()
    host = host.removesuffix(".")
    if not host or len(host) > MAX_HOST_CHARS:
        raise EgressError("invalid_host", "empty or too long hostname")
    try:
        return ipaddress.ip_address(host)  # e.g. full-width digits mapped by UTS-46
    except ValueError:
        pass
    labels = host.split(".")
    for label in labels:
        if not _LABEL_RE.match(label):
            raise EgressError("invalid_host", "invalid hostname")
        if label.startswith("xn--"):
            _check_alabel(label)
        elif label[2:4] == "--":
            raise EgressError("invalid_host", "invalid hostname")
    if _NUMERIC_LABEL_RE.match(labels[-1]):
        raise EgressError("ip_literal_not_allowed", "numeric hostnames are not allowed")
    if host == "localhost" or host.endswith(".localhost"):
        raise EgressError("localhost_not_allowed", "localhost is not allowed")
    return host


def validate_allowlist_entry(raw: str) -> str:
    """Normalize an `egress_hosts` entry: `example.com` or `*.example.com` (subdomains only)."""
    if not isinstance(raw, str):
        raise EgressError("invalid_host", "host must be a string")
    value = raw.strip()
    wildcard = value.startswith("*.")
    rest = value[2:] if wildcard else value
    if "*" in rest:
        raise EgressError("invalid_wildcard", "wildcards are only allowed as a leading '*.'")
    host = normalize_host(rest)
    if not isinstance(host, str):
        raise EgressError("ip_literal_not_allowed", "IP addresses are not allowed")
    if host.count(".") < 1:
        raise EgressError("invalid_host", "host must be a fully qualified domain name")
    if host in PUBLIC_SUFFIXES:
        raise EgressError("public_suffix", "public suffixes are not allowed")
    return f"*.{host}" if wildcard else host


def host_matches(host: str, entry: str) -> bool:
    if entry.startswith("*."):
        suffix = entry[1:]  # ".example.com"
        return host.endswith(suffix) and len(host) > len(suffix)
    return host == entry


def _host_allowed(session: Session, project_id: str, host: str) -> bool:
    entries = session.scalars(select(EgressHost.host).where(EgressHost.project_id == project_id))
    return any(host_matches(host, e) for e in entries)


# --- URL check -----------------------------------------------------------------------------------


def best_effort_host(url: object) -> str | None:
    """Host for audit rows of URLs that failed validation (never userinfo, path or query)."""
    try:
        host = urlsplit(str(url)).hostname
    except ValueError:
        return None
    return host[:200] if host else None


def check_url(
    url: str,
    *,
    project_id: str,
    session: Session,
    purpose: str,
    settings: EgressSettings | None = None,
) -> EgressTarget:
    """Validate `url` under ADR-012 and pin it to one resolved, public IP. Raises `EgressError`."""
    settings = settings or EgressSettings()
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
    host = normalize_host(raw_host)
    port = port if port is not None else _DEFAULT_PORTS[scheme]
    if port not in settings.port_set():
        raise EgressError("port_not_allowed", f"port {port} is not allowed", host=str(host))

    if isinstance(host, str):
        if settings.mode == "allowlist" and not _host_allowed(session, project_id, host):
            raise EgressError("host_not_allowed", f"host {host} is not in the project allowlist", host=host)
        host_str, netloc_host = host, host
        ips = _resolve(host, port)
    else:
        host_str = str(host)
        netloc_host = f"[{host_str}]" if host.version == 6 else host_str
        if settings.mode == "allowlist":
            raise EgressError("ip_literal_not_allowed", "IP addresses are not allowed", host=host_str)
        ips = [host_str]

    for ip in ips:
        reason = _ip_allowed(ip, settings)
        if reason:
            raise EgressError(reason, f"host {host_str} resolves to a blocked address", host=host_str)

    netloc = netloc_host if parts.port is None else f"{netloc_host}:{port}"
    normalized = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    # Guard against parser differentials: httpx must see exactly the host and port we validated.
    try:
        parsed = httpx.URL(normalized)
    except httpx.InvalidURL as exc:
        raise EgressError("invalid_url", "invalid URL", host=host_str) from exc
    if parsed.raw_host.decode("ascii").lower() != host_str or (parsed.port or _DEFAULT_PORTS[scheme]) != port:
        raise EgressError("invalid_url", "ambiguous URL", host=host_str)
    if parsed.userinfo:
        raise EgressError("userinfo_not_allowed", "credentials in the URL are not allowed", host=host_str)
    return EgressTarget(url=normalized, host=host_str, ip=ips[0], port=port)
