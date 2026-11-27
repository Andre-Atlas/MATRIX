"""Operator-trusted local model endpoints (ADR-016): the only exception to the private-IP block.

Hermetic: a throwaway HTTP server on 127.0.0.1 plays the local model server; DNS for named
endpoints is simulated with `set_resolver_for_tests`.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from matrix_server import egress
from matrix_server.egress import (
    EgressError,
    LocalModelSettings,
    check_local_url,
    egress_client,
    local_llm_available,
    local_model_client,
    parse_local_endpoints,
    resolve_local_model_base_url,
)
from matrix_server.storage import models  # noqa: F401  (register tables)
from matrix_server.storage.db import Base, make_engine, make_session_factory
from matrix_server.storage.models import AuditLog, Project


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/tags":
            self._send(200, json.dumps({"models": [{"name": "qwen2.5:0.5b"}]}).encode(),
                       {"Content-Type": "application/json"})
        elif url.path == "/redirect":
            self._send(302, b"", {"Location": q["to"]})
        elif url.path == "/via-proxy":
            self._send(200, b"direct")
        else:
            self._send(404, b"nope")

    do_POST = do_GET


@pytest.fixture(scope="module")
def port():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("MATRIX_EGRESS_", "MATRIX_LOCAL_")):
            monkeypatch.delenv(name)
    yield
    egress.set_resolver_for_tests(None)
    egress.set_dialer_for_tests(None)


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/l.db")
    Base.metadata.create_all(engine)
    f = make_session_factory(engine)
    with f() as s:
        s.add(Project(id="p1", name="p1"))
        s.commit()
    yield f
    engine.dispose()


def _settings(endpoints: str, **kw) -> LocalModelSettings:
    return LocalModelSettings(local_model_endpoints=endpoints, **kw)


# --- allowed -------------------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", ["model", "embedding"])
def test_listed_loopback_endpoint_allowed_for_model_purposes(port, purpose):
    client = local_model_client(purpose=purpose, settings=_settings(f"http://127.0.0.1:{port}"))
    resp = client.get(f"http://127.0.0.1:{port}/api/tags")
    assert resp.status_code == 200
    assert resp.json()["models"][0]["name"] == "qwen2.5:0.5b"


def test_listed_hostname_resolving_to_private_ip_allowed(port):
    seen = []

    def resolver(host, p):
        seen.append(host)
        return ["127.0.0.1"] if host == "ollama" else []

    egress.set_resolver_for_tests(resolver)
    client = local_model_client(purpose="model", settings=_settings(f"http://ollama:{port}"))
    assert client.get(f"http://ollama:{port}/api/tags").status_code == 200
    assert seen == ["ollama"]


def test_from_environment(port, monkeypatch):
    monkeypatch.setenv("MATRIX_LOCAL_MODEL_ENDPOINTS", f" http://127.0.0.1:{port} , http://ollama:11434")
    assert local_model_client(purpose="model").get(f"http://127.0.0.1:{port}/api/tags").is_success


def test_does_not_inherit_proxy_from_environment(port, monkeypatch):
    # A proxy env var pointing nowhere must not be used for the local host (trust_env=False).
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.1:9")
    monkeypatch.setenv("http_proxy", "http://203.0.113.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://203.0.113.1:9")
    client = local_model_client(purpose="model", timeout=5, settings=_settings(f"http://127.0.0.1:{port}"))
    assert client.get(f"http://127.0.0.1:{port}/via-proxy").text == "direct"


# --- refused -------------------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", ["webhook", "scanner", "runner", "benchmark", ""])
def test_other_purposes_get_no_exception(port, purpose):
    s = _settings(f"http://127.0.0.1:{port}")
    with pytest.raises(ValueError):
        local_model_client(purpose=purpose, settings=s)
    with pytest.raises(EgressError) as exc:
        check_local_url(f"http://127.0.0.1:{port}/api/tags",
                        endpoints=parse_local_endpoints(s.local_model_endpoints), purpose=purpose)
    assert exc.value.reason == "purpose_not_allowed"


@pytest.mark.parametrize("purpose", ["webhook", "model", "embedding"])
def test_user_url_client_ignores_the_local_list(port, factory, monkeypatch, purpose):
    monkeypatch.setenv("MATRIX_LOCAL_MODEL_ENDPOINTS", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("MATRIX_EGRESS_MODE", "public")
    monkeypatch.setenv("MATRIX_EGRESS_ALLOWED_PORTS", str(port))
    client = egress_client(project_id="p1", session_factory=factory, purpose=purpose)
    with pytest.raises(EgressError) as exc:
        client.get(f"http://127.0.0.1:{port}/api/tags")
    assert exc.value.reason == "private_address"


def test_other_port_refused(port):
    client = local_model_client(purpose="model", settings=_settings(f"http://127.0.0.1:{port}"))
    with pytest.raises(EgressError) as exc:
        client.get(f"http://127.0.0.1:{port + 1}/api/tags")
    assert exc.value.reason == "local_endpoint_not_allowed"


def test_default_port_does_not_match_explicit_other_port(port):
    client = local_model_client(purpose="model", settings=_settings("http://127.0.0.1:11434"))
    with pytest.raises(EgressError) as exc:
        client.get("http://127.0.0.1/api/tags")
    assert exc.value.reason == "local_endpoint_not_allowed"


def test_scheme_must_match(port):
    client = local_model_client(purpose="model", settings=_settings(f"http://127.0.0.1:{port}"))
    with pytest.raises(EgressError) as exc:
        client.get(f"https://127.0.0.1:{port}/api/tags")
    assert exc.value.reason == "local_endpoint_not_allowed"


@pytest.mark.parametrize(
    "url",
    ["http://10.0.0.5:{p}/api/tags", "http://127.0.0.2:{p}/api/tags", "http://[::1]:{p}/api/tags",
     "http://postgres:{p}/", "http://localhost:{p}/api/tags"],
)
def test_unlisted_private_host_refused(port, url):
    egress.set_resolver_for_tests(lambda h, p: ["172.18.0.3"])
    client = local_model_client(purpose="model", settings=_settings(f"http://127.0.0.1:{port}"))
    with pytest.raises(EgressError) as exc:
        client.get(url.format(p=port))
    assert exc.value.reason == "local_endpoint_not_allowed"


@pytest.mark.parametrize(
    "to",
    ["http://10.0.0.7:{p}/x", "http://127.0.0.2:{p}/x", "http://127.0.0.1:{q}/x", "http://93.184.216.34/x",
     "http://169.254.169.254/latest/meta-data/"],
)
def test_redirect_off_the_list_refused(port, to):
    client = local_model_client(purpose="model", settings=_settings(f"http://127.0.0.1:{port}"))
    target = to.format(p=port, q=port + 1)
    with pytest.raises(EgressError) as exc:
        client.get(f"http://127.0.0.1:{port}/redirect?to={target}")
    assert exc.value.reason == "local_endpoint_not_allowed"


def test_redirect_within_the_list_followed(port):
    client = local_model_client(purpose="model", settings=_settings(f"http://127.0.0.1:{port}"))
    resp = client.get(f"http://127.0.0.1:{port}/redirect?to=/api/tags")
    assert resp.status_code == 200


def test_metadata_and_link_local_refused_even_if_listed():
    eps = parse_local_endpoints("http://169.254.169.254:80,http://[fe80::1]:11434,http://meta:11434")
    for url in ("http://169.254.169.254/latest/", "http://[fe80::1]:11434/api/tags"):
        with pytest.raises(EgressError) as exc:
            check_local_url(url, endpoints=eps, purpose="model")
        assert exc.value.reason in ("forbidden_address", "invalid_host")
    egress.set_resolver_for_tests(lambda h, p: ["169.254.169.254"])
    with pytest.raises(EgressError) as exc:
        check_local_url("http://meta:11434/api/tags", endpoints=eps, purpose="model")
    assert exc.value.reason == "forbidden_address"


def test_credentials_in_url_refused(port):
    client = local_model_client(purpose="model", settings=_settings(f"http://127.0.0.1:{port}"))
    with pytest.raises(EgressError) as exc:
        client.get(f"http://u:p@127.0.0.1:{port}/api/tags")
    assert exc.value.reason == "userinfo_not_allowed"


def test_empty_setting_allows_nothing(port):
    for raw in ("", " , ", "ftp://127.0.0.1:21,not a url"):
        client = local_model_client(purpose="model", settings=_settings(raw))
        assert client.endpoints == ()
        with pytest.raises(EgressError) as exc:
            client.get(f"http://127.0.0.1:{port}/api/tags")
        assert exc.value.reason == "local_endpoint_not_allowed"
    assert resolve_local_model_base_url(_settings("")) is None
    assert local_llm_available(0.5, _settings("")) is False


def test_default_is_empty():
    assert LocalModelSettings().local_model_endpoints == ""
    assert local_model_client(purpose="model").endpoints == ()


def test_list_is_frozen_and_not_settable_per_request(port, monkeypatch):
    client = local_model_client(purpose="model", settings=_settings("http://127.0.0.1:11434"))
    monkeypatch.setenv("MATRIX_LOCAL_MODEL_ENDPOINTS", f"http://127.0.0.1:{port}")
    with pytest.raises(EgressError):
        client.get(f"http://127.0.0.1:{port}/api/tags")
    with pytest.raises(AttributeError):
        client.endpoints = parse_local_endpoints(f"http://127.0.0.1:{port}")  # type: ignore[misc]
    # Neither the factory nor a request takes an endpoint list: only operator settings do.
    for fn in (local_model_client, client.request, client.get, client.post):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"endpoints", "allow", "allowlist", "local_model_endpoints", "base_url"}


# --- audit / helpers -----------------------------------------------------------------------------


def test_audit_rows_like_egress(port, factory):
    client = local_model_client(purpose="embedding", project_id="p1", session_factory=factory,
                                settings=_settings(f"http://127.0.0.1:{port}"))
    client.post(f"http://127.0.0.1:{port}/api/tags?secret=1", json={"input": "hello"})
    with pytest.raises(EgressError):
        client.get(f"http://10.0.0.1:{port}/x")
    with factory() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "egress.request").order_by(AuditLog.id)).all()
    details = [r.detail for r in rows]
    assert {"purpose": "embedding", "local": True, "status": 200} in details
    assert {"purpose": "embedding", "local": True, "reason": "local_endpoint_not_allowed"} in details
    blob = json.dumps(details) + " ".join(r.target or "" for r in rows)
    assert "secret" not in blob and "hello" not in blob and "/api/tags" not in blob


def test_resolve_base_url_requires_membership(port):
    s = _settings(f"http://127.0.0.1:{port},http://ollama:11434", local_llm_url=f"http://127.0.0.1:{port}/")
    assert resolve_local_model_base_url(s) == f"http://127.0.0.1:{port}"
    s = _settings("http://ollama:11434", local_llm_url="http://OLLAMA:11434/v1/")
    assert resolve_local_model_base_url(s) == "http://ollama:11434/v1"
    assert resolve_local_model_base_url(_settings("http://ollama:11434")) is None  # default 127.0.0.1
    assert resolve_local_model_base_url(_settings("http://ollama:11434", local_llm_url="nope")) is None


def test_default_model_names(monkeypatch):
    s = LocalModelSettings()
    assert (s.local_llm_url, s.local_chat_model, s.local_embed_model, s.local_vision_model) == (
        "http://127.0.0.1:11434", "qwen2.5:0.5b", "all-minilm", "moondream")
    monkeypatch.setenv("MATRIX_LOCAL_CHAT_MODEL", "llama3.2:1b")
    assert LocalModelSettings().local_chat_model == "llama3.2:1b"


def test_local_llm_available(port):
    url = f"http://127.0.0.1:{port}"
    assert local_llm_available(2.0, _settings(url, local_llm_url=url)) is True
    assert local_llm_available(2.0, _settings(url)) is False  # LLM URL (default :11434) not listed
    dead = "http://127.0.0.1:9"  # discard port: nothing listens
    assert local_llm_available(0.5, _settings(dead, local_llm_url=dead)) is False
