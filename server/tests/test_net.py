"""Proxy settings for outbound clients are read, not rewritten (T3: `main`'s `_clean_no_proxy`
mutated NO_PROXY in `os.environ` on every provider call)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from matrix_server.evals.providers import OpenAICompatibleProvider
from matrix_server.net import client_kwargs, proxy_mounts

NO_PROXY = "localhost,[::1],127.0.0.1,.internal.example,10.0.0.0/8"


@pytest.fixture
def proxy_env(monkeypatch):
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    # A dead proxy: anything routed through it fails, so a success proves the bypass.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", NO_PROXY)


def test_raw_httpx_fails_on_bracketed_ipv6_no_proxy(proxy_env):
    """The bug `main` worked around (httpx 0.28): keep it visible so the workaround can go away."""
    with pytest.raises(httpx.InvalidURL):
        httpx.Client()


def test_client_kwargs_route_no_proxy_hosts_directly(proxy_env):
    with httpx.Client(**client_kwargs()) as client:
        direct = client._transport
        # (CIDR entries are passed through as httpx itself does: it does not match them)
        for url in ("http://[::1]:8000/", "http://127.0.0.1:8000/", "https://api.internal.example/x"):
            assert client._transport_for_url(httpx.URL(url)) is direct, url
        proxied = client._transport_for_url(httpx.URL("https://api.openai.com/v1"))
        assert proxied is not direct
    import os

    assert os.environ["NO_PROXY"] == NO_PROXY


def test_no_proxy_star_and_no_proxy_vars(monkeypatch):
    assert proxy_mounts({"NO_PROXY": "*", "HTTPS_PROXY": "http://p:1"}) is None
    assert proxy_mounts({}) is None
    mounts = proxy_mounts({"https_proxy": "p:3128", "HTTPS_PROXY": "http://ignored:1", "no_proxy": "bad host,[::1]"})
    assert set(mounts) == {"https://", "all://[::1]"}


def test_custom_transport_is_used_as_is():
    t = httpx.MockTransport(lambda r: httpx.Response(200))
    assert client_kwargs(t) == {"transport": t, "trust_env": False}


def test_provider_call_bypasses_proxy_and_leaves_environment_untouched(proxy_env):
    import os

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            body = json.dumps({
                "choices": [{"message": {"content": "pong"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        before = dict(os.environ)
        provider = OpenAICompatibleProvider(base_url=f"http://127.0.0.1:{server.server_port}/v1", max_retries=0)
        out = provider.complete([{"role": "user", "content": "ping"}], "m")
        assert out.text == "pong"
        assert dict(os.environ) == before
    finally:
        server.shutdown()
