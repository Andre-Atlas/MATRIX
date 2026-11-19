"""(Copy of matrix_server.net; the SDK does not depend on the server.) Outbound HTTP client settings that honour the proxy environment without mutating it.

httpx (0.28) turns each `NO_PROXY` entry into a URL pattern and fails on a bracketed IPv6 entry
such as `[::1]` (`InvalidURL: Invalid port`). `main` worked around it by rewriting `NO_PROXY` in
`os.environ` on every call, which changes the environment of the whole process (other libraries,
subprocesses, other threads) as a side effect. Here the proxy configuration is read from the
environment, sanitised locally and handed to the client as explicit `mounts`, with
`trust_env=False` so httpx never parses the raw variables itself.
"""

from __future__ import annotations

import ipaddress
import os
import ssl
from collections.abc import Mapping
from typing import Any

import httpx


def _get(environ: Mapping[str, str], name: str) -> str:
    # urllib.request.getproxies() semantics: the lowercase variable wins over the uppercase one.
    return (environ.get(name.lower()) or environ.get(name.upper()) or "").strip()


def _no_proxy_pattern(entry: str) -> str | None:
    host = entry.strip()
    if not host:
        return None
    if host.startswith("[") and "]" in host:  # "[::1]" or "[::1]:8080" -> "::1"
        host = host[1 : host.index("]")]
    try:
        net = ipaddress.ip_network(host, strict=False)
    except ValueError:
        net = None
    if net is not None:
        if net.version == 6:
            return f"all://[{host}]"
        return f"all://{host}"
    if host == "localhost":
        return "all://localhost"
    if any(c in host for c in "/[]@ "):
        return None  # not a hostname httpx can match; ignore it rather than fail the request
    return f"all://*{host}"


def proxy_mounts(
    environ: Mapping[str, str] | None = None, *, verify: Any = True
) -> dict[str, httpx.BaseTransport | None] | None:
    """httpx `mounts` equivalent to the environment's proxy settings. None means "no proxy at all"
    (NO_PROXY=*). Hosts in NO_PROXY map to None, i.e. the client's own direct transport."""
    environ = os.environ if environ is None else environ
    no_proxy = _get(environ, "no_proxy")
    if no_proxy == "*":
        return None
    mounts: dict[str, httpx.BaseTransport | None] = {}
    all_proxy = _get(environ, "all_proxy")
    for scheme in ("http", "https"):
        proxy = _get(environ, f"{scheme}_proxy") or all_proxy
        if proxy:
            if "://" not in proxy:
                proxy = f"http://{proxy}"
            mounts[f"{scheme}://"] = httpx.HTTPTransport(proxy=proxy, verify=verify)
    if not mounts:
        return None
    for entry in no_proxy.split(","):
        pattern = _no_proxy_pattern(entry)
        if pattern is None:
            continue
        try:
            httpx.URL(pattern)  # what httpx's URLPattern parses; skip what it would reject
        except (httpx.InvalidURL, ValueError):
            continue
        mounts[pattern] = None
    return mounts


def client_kwargs(transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Keyword arguments for `httpx.Client` (or a custom `transport`, e.g. in tests, as is)."""
    if transport is not None:
        return {"transport": transport, "trust_env": False}
    # trust_env=False also stops httpx reading SSL_CERT_FILE/SSL_CERT_DIR: build that context here.
    verify: ssl.SSLContext = httpx.create_ssl_context(trust_env=True)
    return {"trust_env": False, "verify": verify, "mounts": proxy_mounts(verify=verify)}
