"""Egress policy (ADR-012): blocked ranges, DNS pinning, redirects, limits, allowlist, audit, API.

Loopback is blocked by the policy, so tests that need the local HTTP server either:
- set `MATRIX_EGRESS_ALLOW_PRIVATE=1` (client mechanics only), or
- simulate a public IP with `set_resolver_for_tests` and route the pinned dial to the local
  server with `set_dialer_for_tests` (the policy runs on the simulated public IP).
"""

from __future__ import annotations

import gzip
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from matrix_server import egress
from matrix_server.auth import Principal, Role
from matrix_server.egress import (
    EgressError,
    EgressHost,
    EgressSettings,
    check_url,
    classify_ip,
    egress_client,
    validate_allowlist_entry,
)
from matrix_server.storage import models  # noqa: F401  (register tables)
from matrix_server.storage.db import Base, make_engine, make_session_factory
from matrix_server.storage.models import AuditLog, Project

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")
PUBLIC_IP = "93.184.216.34"
ADMIN = {"Authorization": "Bearer adm"}


# --- local HTTP server --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if "Content-Length" not in (headers or {}) and body is not None:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/ok":
            self._send(200, b"hello", {"Content-Type": "text/plain"})
        elif url.path == "/echo":
            seen = {k.lower(): v for k, v in self.headers.items()}
            self._send(200, json.dumps(seen).encode(), {"Content-Type": "application/json"})
        elif url.path == "/redirect":
            self._send(int(q.get("code", "302")), b"", {"Location": q["to"]})
        elif url.path == "/big":
            self._send(200, b"x" * int(q["n"]))
        elif url.path == "/stream":
            self.send_response(200)
            self.end_headers()  # HTTP/1.0: no length, read until close
            for _ in range(int(q["n"])):
                self.wfile.write(b"y" * 65536)
        elif url.path == "/gzip":
            payload = gzip.compress(b"\0" * int(q["n"]))
            self._send(200, payload, {"Content-Encoding": "gzip"})
        elif url.path == "/slow":
            time.sleep(float(q.get("s", "2")))
            self._send(200, b"late")
        else:
            self._send(404, b"nope")

    do_POST = do_GET


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # client aborts on capped responses
        pass


@pytest.fixture(scope="module")
def server():
    srv = _QuietServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("MATRIX_EGRESS_"):
            monkeypatch.delenv(name)
    yield
    egress.set_resolver_for_tests(None)
    egress.set_dialer_for_tests(None)


@pytest.fixture(params=["sqlite", "pg"])
def db(request, tmp_path):
    if request.param == "pg":
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        url = PG_URL
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
        eng.dispose()
    else:
        url = f"sqlite:///{tmp_path}/e.db"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        s.add_all([Project(id="p1", name="p1"), Project(id="p2", name="p2")])
        s.commit()
    yield factory
    engine.dispose()


def _allow(factory, *hosts, project="p1"):
    with factory() as s:
        for h in hosts:
            s.add(EgressHost(project_id=project, host=validate_allowlist_entry(h)))
        s.commit()


def _audit_rows(factory, action="egress.request"):
    with factory() as s:
        return list(s.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.ts)))


class _Net:
    """Simulated DNS + dial routing to the local server; records lookups and dialed IPs."""

    def __init__(self, port, table):
        self.port, self.table, self.lookups, self.dials = port, table, [], []
        egress.set_resolver_for_tests(self.resolve)
        egress.set_dialer_for_tests(self.dial)

    def resolve(self, host, port):
        self.lookups.append(host)
        value = self.table[host]
        return value(len(self.lookups)) if callable(value) else value

    def dial(self, ip, port):
        self.dials.append(ip)
        return "127.0.0.1", self.port


def _public():
    return EgressSettings(mode="public")


# --- IP classification --------------------------------------------------------------------------

BLOCKED = [
    "0.0.0.0", "0.1.2.3", "10.0.0.1", "100.64.0.1", "100.127.255.254", "127.0.0.1", "127.1.2.3",
    "169.254.169.254", "169.254.0.1", "172.16.0.1", "172.31.255.255", "192.0.0.1", "192.0.0.9",
    "192.0.2.1", "192.168.1.1", "198.18.0.1", "198.19.255.255", "198.51.100.1", "203.0.113.1",
    "224.0.0.1", "224.0.1.1", "239.255.255.250", "240.0.0.1", "255.255.255.255",
    "::", "::1", "fc00::1", "fd12:3456::1", "fe80::1", "ff02::1", "ff0e::1", "fec0::1",
    "64:ff9b::7f00:1", "64:ff9b::808:808", "64:ff9b:1::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
    "::ffff:169.254.169.254", "::ffff:0.0.0.0", "::127.0.0.1", "::a9fe:a9fe", "2002:7f00:1::1",
    "2002:a9fe:a9fe::1", "2002:0a00:0001::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "2001:db8::1",
    "100::1",
]
PUBLIC = ["8.8.8.8", "1.1.1.1", PUBLIC_IP, "2606:4700:4700::1111", "2001:4860:4860::8888", "::ffff:8.8.8.8"]


@pytest.mark.parametrize("ip", BLOCKED)
def test_blocked_ranges(ip, db):
    assert classify_ip(ip) is not None
    egress.set_resolver_for_tests(lambda h, p: [ip])
    with db() as s, pytest.raises(EgressError) as exc:
        check_url("https://svc.example.com/x", project_id="p1", session=s, purpose="t", settings=_public())
    assert exc.value.reason in ("private_address", "forbidden_address")
    # The same address as a URL literal is blocked too.
    literal = f"[{ip}]" if ":" in ip else ip
    with db() as s, pytest.raises(EgressError):
        check_url(f"http://{literal}/", project_id="p1", session=s, purpose="t", settings=_public())


@pytest.mark.parametrize("ip", PUBLIC)
def test_public_addresses_pass(ip, db):
    assert classify_ip(ip) is None
    egress.set_resolver_for_tests(lambda h, p: [ip])
    with db() as s:
        target = check_url("https://svc.example.com/x?a=1", project_id="p1", session=s, purpose="t",
                           settings=_public())
    assert (target.host, target.ip, target.port) == ("svc.example.com", ip, 443)


@pytest.mark.parametrize("ip", ["169.254.169.254", "fe80::1", "0.0.0.0", "224.0.0.1", "::ffff:169.254.169.254"])
def test_allow_private_never_unlocks_metadata_or_multicast(ip, db, monkeypatch):
    monkeypatch.setenv("MATRIX_EGRESS_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("MATRIX_EGRESS_MODE", "public")
    egress.set_resolver_for_tests(lambda h, p: [ip])
    with db() as s, pytest.raises(EgressError) as exc:
        check_url("http://svc.example.com/", project_id="p1", session=s, purpose="t")
    assert exc.value.reason == "forbidden_address"


def test_allow_private_comes_only_from_settings(db):
    assert EgressSettings().allow_private is False and EgressSettings().mode == "allowlist"
    egress.set_resolver_for_tests(lambda h, p: ["127.0.0.1"])
    with db() as s, pytest.raises(EgressError):
        check_url("http://svc.example.com/?allow_private=1", project_id="p1", session=s, purpose="t",
                  settings=_public())


def test_mixed_public_and_private_dns_is_blocked(db):
    egress.set_resolver_for_tests(lambda h, p: ["8.8.8.8", "10.0.0.7"])
    with db() as s, pytest.raises(EgressError) as exc:
        check_url("https://svc.example.com/", project_id="p1", session=s, purpose="t", settings=_public())
    assert exc.value.reason == "private_address"


def test_dns_failure(db):
    def boom(h, p):
        raise OSError("nxdomain")

    egress.set_resolver_for_tests(boom)
    with db() as s, pytest.raises(EgressError) as exc:
        check_url("https://nope.example.com/", project_id="p1", session=s, purpose="t", settings=_public())
    assert exc.value.reason == "dns_failure"


# --- URL and host validation --------------------------------------------------------------------

BAD_URLS = [
    ("file:///etc/passwd", "scheme_not_allowed"),
    ("gopher://svc.example.com:70/_x", "scheme_not_allowed"),
    ("ftp://svc.example.com/", "scheme_not_allowed"),
    ("javascript:alert(1)", "scheme_not_allowed"),
    ("dict://svc.example.com:11211/", "scheme_not_allowed"),
    ("http://user:pass@svc.example.com/", "userinfo_not_allowed"),
    ("http://user@svc.example.com/", "userinfo_not_allowed"),
    ("http://svc.example.com:8080/", "port_not_allowed"),
    ("http://svc.example.com:22/", "port_not_allowed"),
    ("https://svc.example.com:0/", "port_not_allowed"),
    ("http://svc.example.com:99999/", "invalid_url"),
    ("http:///path", "invalid_host"),
    ("http://", "invalid_host"),
    ("http://svc.example.com\\@evil.com/", "invalid_url"),
    ("http://svc.example.com /", "invalid_url"),
    ("http://svc.exa\nmple.com/", "invalid_url"),
    ("http://" + "a" * 2050 + ".com/", "invalid_url"),
    ("http://" + ".".join(["a" * 60] * 5) + "/", "invalid_host"),
    ("http://2130706433/", "ip_literal_not_allowed"),
    ("http://0x7f.1/", "ip_literal_not_allowed"),
    ("http://127.1/", "ip_literal_not_allowed"),
    ("http://1.0x7f/", "ip_literal_not_allowed"),
    ("http://localhost/", "localhost_not_allowed"),
    ("http://LOCALHOST./", "localhost_not_allowed"),
    ("http://ｌｏｃａｌｈｏｓｔ/", "localhost_not_allowed"),
    ("http://api.localhost/", "localhost_not_allowed"),
    ("http://раураl.com/", "invalid_host"),  # Cyrillic + Latin
    ("http://xn--pypal-4ve.com/", "invalid_host"),  # mixed-script punycode
    ("http://xn--abc.com/", "invalid_host"),  # bogus punycode
    ("http://a..com/", "invalid_host"),
    ("http://-a.com/", "invalid_host"),
    ("http://[fe80::1%25eth0]/", "invalid_host"),
]


@pytest.mark.parametrize(("url", "reason"), BAD_URLS)
def test_bad_urls_rejected(url, reason, db):
    egress.set_resolver_for_tests(lambda h, p: [PUBLIC_IP])
    with db() as s, pytest.raises(EgressError) as exc:
        check_url(url, project_id="p1", session=s, purpose="t", settings=_public())
    assert exc.value.reason == reason
    assert "pass" not in str(exc.value)


def test_error_message_has_no_query_or_credentials(db):
    egress.set_resolver_for_tests(lambda h, p: ["10.0.0.1"])
    with db() as s, pytest.raises(EgressError) as exc:
        check_url("https://svc.example.com/p?token=s3cr3t", project_id="p1", session=s, purpose="t",
                  settings=_public())
    assert "s3cr3t" not in str(exc.value) and "/p" not in str(exc.value)


def test_hostname_normalization(db):
    egress.set_resolver_for_tests(lambda h, p: [PUBLIC_IP])
    _allow(db, "svc.example.com", "пример.com")
    with db() as s:
        a = check_url("HTTPS://SVC.Example.COM./x", project_id="p1", session=s, purpose="t")
        b = check_url("http://ПРИМЕР.com/", project_id="p1", session=s, purpose="t")
        c = check_url("http://xn--e1afmkfd.com/", project_id="p1", session=s, purpose="t")
    assert a.host == "svc.example.com" and a.url == "https://svc.example.com/x"
    assert b.host == c.host == "xn--e1afmkfd.com"


def test_custom_allowed_ports(db, monkeypatch):
    monkeypatch.setenv("MATRIX_EGRESS_ALLOWED_PORTS", "443,8443")
    monkeypatch.setenv("MATRIX_EGRESS_MODE", "public")
    egress.set_resolver_for_tests(lambda h, p: [PUBLIC_IP])
    with db() as s:
        assert check_url("https://svc.example.com:8443/", project_id="p1", session=s, purpose="t").port == 8443
        with pytest.raises(EgressError):
            check_url("http://svc.example.com/", project_id="p1", session=s, purpose="t")


# --- allowlist ----------------------------------------------------------------------------------


def test_allowlist_default_mode_blocks_unlisted_without_dns(db):
    calls = []
    egress.set_resolver_for_tests(lambda h, p: calls.append(h) or [PUBLIC_IP])
    with db() as s, pytest.raises(EgressError) as exc:
        check_url("https://svc.example.com/", project_id="p1", session=s, purpose="t")
    assert exc.value.reason == "host_not_allowed" and calls == []


def test_allowlist_exact_wildcard_and_bare_suffix(db):
    egress.set_resolver_for_tests(lambda h, p: [PUBLIC_IP])
    _allow(db, "api.exact.com", "*.wild.com")
    _allow(db, "other.com", project="p2")
    ok = ["https://api.exact.com/", "https://a.wild.com/", "https://a.b.wild.com/"]
    blocked = ["https://x.api.exact.com/", "https://exact.com/", "https://wild.com/", "https://evilwild.com/",
               "https://other.com/", "https://wild.com.evil.com/"]
    with db() as s:
        for url in ok:
            check_url(url, project_id="p1", session=s, purpose="t")
        for url in blocked:
            with pytest.raises(EgressError) as exc:
                check_url(url, project_id="p1", session=s, purpose="t")
            assert exc.value.reason == "host_not_allowed", url


def test_allowlist_mode_rejects_ip_literals(db):
    with db() as s, pytest.raises(EgressError) as exc:
        check_url("https://8.8.8.8/", project_id="p1", session=s, purpose="t")
    assert exc.value.reason == "ip_literal_not_allowed"


@pytest.mark.parametrize(
    "entry",
    ["*", "*.", "*.com", "*.co.uk", "*.github.io", "co.uk", "com", "intranet", "*.*.example.com",
     "a.*.example.com", "*example.com", "ex*mple.com", "127.0.0.1", "[::1]", "::1", "localhost",
     "*.localhost", "2130706433", "http://example.com", "example.com/path", "example.com:443",
     "user@example.com", "", " ", "раураl.com"],
)
def test_allowlist_entry_validation_rejects(entry):
    with pytest.raises(EgressError):
        validate_allowlist_entry(entry)


@pytest.mark.parametrize(
    ("entry", "normalized"),
    [("Example.COM.", "example.com"), ("*.Example.com", "*.example.com"), ("пример.com", "xn--e1afmkfd.com"),
     ("*.svc.co.uk", "*.svc.co.uk"), ("a-b.example.io", "a-b.example.io")],
)
def test_allowlist_entry_validation_normalizes(entry, normalized):
    assert validate_allowlist_entry(entry) == normalized


# --- client mechanics ---------------------------------------------------------------------------


def _client(db, **kw):
    return egress_client(project_id="p1", session_factory=db, purpose="webhook", **kw)


def test_request_pins_ip_and_sends_original_host(db, server):
    net = _Net(server, {"api.example.com": [PUBLIC_IP]})
    _allow(db, "api.example.com")
    resp = _client(db).request("GET", "http://api.example.com/echo?token=s3cr3t")
    assert resp.status_code == 200
    assert resp.json()["host"] == "api.example.com"
    assert net.dials == [PUBLIC_IP] and net.lookups == ["api.example.com"]


def test_dns_rebinding_is_not_re_resolved(db, server):
    net = _Net(server, {"api.example.com": lambda n: [PUBLIC_IP] if n == 1 else ["127.0.0.1"]})
    resp = _client(db, settings=_public()).get("http://api.example.com/ok")
    assert resp.content == b"hello"
    assert net.lookups == ["api.example.com"]  # resolved once
    assert net.dials == [PUBLIC_IP]  # dialed the validated IP, never the rebound one


def test_real_loopback_server_with_allow_private(db, server, monkeypatch):
    monkeypatch.setenv("MATRIX_EGRESS_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("MATRIX_EGRESS_MODE", "public")
    monkeypatch.setenv("MATRIX_EGRESS_ALLOWED_PORTS", str(server))
    resp = _client(db).post(f"http://127.0.0.1:{server}/echo", json={"a": 1})
    assert resp.status_code == 200 and resp.json()["content-type"] == "application/json"


def test_loopback_blocked_without_allow_private(db, server, monkeypatch):
    monkeypatch.setenv("MATRIX_EGRESS_MODE", "public")
    monkeypatch.setenv("MATRIX_EGRESS_ALLOWED_PORTS", str(server))
    with pytest.raises(EgressError) as exc:
        _client(db).get(f"http://127.0.0.1:{server}/ok")
    assert exc.value.reason == "private_address"


def test_redirect_to_metadata_blocked_at_hop(db, server, monkeypatch):
    monkeypatch.setenv("MATRIX_EGRESS_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("MATRIX_EGRESS_MODE", "public")
    net = _Net(server, {"api.example.com": ["127.0.0.1"]})
    with pytest.raises(EgressError) as exc:
        _client(db).get("http://api.example.com/redirect?to=http://169.254.169.254/latest/meta-data/")
    assert exc.value.reason == "forbidden_address"
    assert len(net.dials) == 1
    rows = _audit_rows(db)
    assert [r.detail for r in rows] == [
        {"purpose": "webhook", "status": 302},
        {"purpose": "webhook", "reason": "forbidden_address"},
    ]
    assert [r.target for r in rows] == ["api.example.com", "169.254.169.254"]


@pytest.mark.parametrize("to", ["http://10.0.0.5/", "http://internal.example.com/", "http://[::1]/",
                                "file:///etc/passwd", "http://api.example.com:8080/"])
def test_redirect_to_private_blocked_at_hop(db, server, to):
    net = _Net(server, {"api.example.com": [PUBLIC_IP], "internal.example.com": ["10.1.2.3"]})
    with pytest.raises(EgressError):
        _client(db, settings=_public()).get(f"http://api.example.com/redirect?to={to}")
    assert net.dials == [PUBLIC_IP]


def test_redirect_revalidates_allowlist(db, server):
    net = _Net(server, {"api.example.com": [PUBLIC_IP], "elsewhere.com": [PUBLIC_IP]})
    _allow(db, "api.example.com")
    with pytest.raises(EgressError) as exc:
        _client(db).get("http://api.example.com/redirect?to=http://elsewhere.com/ok")
    assert exc.value.reason == "host_not_allowed" and net.dials == [PUBLIC_IP]


def test_redirect_follows_and_strips_caller_headers_on_origin_change(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP], "b.example.com": ["8.8.8.8"]})
    secret_headers = {
        "Authorization": "Bearer tok", "Proxy-Authorization": "Basic x", "Cookie": "sid=1",
        "X-Api-Key": "k1", "Api-Key": "k2", "X-Goog-Api-Key": "k3", "Anthropic-Api-Key": "k4",
        "X-Custom-Token": "k5",
    }
    kept = {"Accept": "application/json", "Accept-Language": "pt-BR", "User-Agent": "matrix-test"}
    hdrs = {**secret_headers, **kept}
    same = _client(db, settings=_public()).get("http://a.example.com/redirect?to=/echo", headers=hdrs).json()
    assert same["authorization"] == "Bearer tok" and same["x-api-key"] == "k1"  # same origin: all kept
    other = _client(db, settings=_public()).get(
        "http://a.example.com/redirect?to=http://b.example.com/echo", headers=hdrs
    ).json()
    for name in secret_headers:
        assert name.lower() not in other, name
    for name, value in kept.items():
        assert other[name.lower()] == value
    assert other["host"] == "b.example.com"


def test_redirect_to_other_port_is_cross_origin(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP]})
    other = _client(db, settings=EgressSettings(mode="public", allowed_ports="80,8080")).get(
        "http://a.example.com/redirect?to=http://a.example.com:8080/echo", headers={"X-Api-Key": "k1"}
    ).json()
    assert "x-api-key" not in other


def test_cross_origin_post_keeps_content_type_with_307(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP], "b.example.com": ["8.8.8.8"]})
    seen = _client(db, settings=_public()).post(
        "http://a.example.com/redirect?code=307&to=http://b.example.com/echo",
        json={"x": 1}, headers={"X-Api-Key": "k1"},
    ).json()
    assert seen["content-type"] == "application/json" and "x-api-key" not in seen


def test_redirect_limit(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP]})
    hop = "/redirect?to=" + "/redirect%3Fto%3D" * 1 + "/ok"
    assert _client(db, settings=_public()).get("http://a.example.com" + hop).content == b"hello"
    loop = "http://a.example.com/redirect?to=/redirect%3Fto%3D/redirect%253Fto%253D/redirect%25253Fto%25253D/ok"
    with pytest.raises(EgressError) as exc:
        _client(db, settings=_public(), max_redirects=2).get(loop)
    assert exc.value.reason == "too_many_redirects"


def test_303_switches_to_get_and_drops_body(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP]})
    resp = _client(db, settings=_public()).post(
        "http://a.example.com/redirect?code=303&to=/echo", json={"x": 1}
    )
    assert "content-type" not in resp.json()


@pytest.mark.parametrize("path", ["/big?n=5000", "/stream?n=4", "/gzip?n=10000000"])
def test_response_size_cap(db, server, path):
    _Net(server, {"a.example.com": [PUBLIC_IP]})
    with pytest.raises(EgressError) as exc:
        _client(db, settings=_public(), max_response_bytes=4096).get("http://a.example.com" + path)
    assert exc.value.reason == "response_too_large"


def test_gzip_within_limit_is_decoded(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP]})
    resp = _client(db, settings=_public(), max_response_bytes=4096).get("http://a.example.com/gzip?n=1000")
    assert resp.content == b"\0" * 1000


def test_timeout(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP]})
    start = time.monotonic()
    with pytest.raises(EgressError) as exc:
        _client(db, settings=_public(), timeout=0.5).get("http://a.example.com/slow?s=3")
    assert exc.value.reason == "timeout" and time.monotonic() - start < 2.5


def test_request_size_cap(db, server):
    _Net(server, {"a.example.com": [PUBLIC_IP]})
    settings = EgressSettings(mode="public", max_request_bytes=100)
    with pytest.raises(EgressError) as exc:
        _client(db, settings=settings).post("http://a.example.com/echo", content=b"z" * 101)
    assert exc.value.reason == "request_too_large"
    assert _audit_rows(db)[-1].detail == {"purpose": "webhook", "reason": "request_too_large"}


def test_audit_rows_have_no_url_path_query_or_headers(db, server):
    _Net(server, {"api.example.com": [PUBLIC_IP]})
    _allow(db, "api.example.com")
    _client(db).get("http://api.example.com/echo?token=s3cr3t", headers={"Authorization": "Bearer k"})
    with pytest.raises(EgressError):
        _client(db).get("http://blocked.example.com/x?token=s3cr3t")
    with pytest.raises(EgressError):
        _client(db).get("gopher://user:pw@evil.example.com/x?token=s3cr3t")
    rows = _audit_rows(db)
    assert [(r.project_id, r.target, r.detail) for r in rows] == [
        ("p1", "api.example.com", {"purpose": "webhook", "status": 200}),
        ("p1", "blocked.example.com", {"purpose": "webhook", "reason": "host_not_allowed"}),
        ("p1", "evil.example.com", {"purpose": "webhook", "reason": "scheme_not_allowed"}),
    ]
    dump = json.dumps([[r.target, r.detail] for r in rows])
    for leaked in ("s3cr3t", "/echo", "/x", "Bearer", "pw"):
        assert leaked not in dump


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture(params=["sqlite", "pg"])
def api(request, tmp_path):
    # Imported lazily so the policy/client tests do not depend on every router importing cleanly.
    from matrix_server.api import egress as egress_api
    from matrix_server.app import create_app
    from matrix_server.config import Settings

    if request.param == "pg":
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        url = PG_URL
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
        eng.dispose()
    else:
        url = f"sqlite:///{tmp_path}/api.db"
    app = create_app(Settings(database_url=url, admin_token="adm"))
    client = TestClient(app)
    keys = {}
    for name in ("alpha", "beta"):
        resp = client.post("/api/v1/admin/projects", json={"name": name}, headers=ADMIN)
        assert resp.status_code == 201
        keys[name] = (resp.json()["project"]["id"] if "project" in resp.json() else resp.json()["id"],
                      resp.json()["api_key"])
    state = {"principal": None}
    app.dependency_overrides[egress_api.admin_principal] = lambda: state["principal"]
    try:
        yield client, app, keys, state
    finally:
        app.state.db_engine.dispose()


def _as_admin(state, project_id, user="u1"):
    state["principal"] = Principal(project_id, "user", user, Role.ADMIN, frozenset({"ingest", "read", "write"}))


def test_api_requires_admin(api):
    client, app, keys, _ = api
    pid, key = keys["alpha"]
    app.dependency_overrides.clear()
    path = f"/api/v1/projects/{pid}/egress-hosts"
    assert client.get(path).status_code == 401
    # An API key is never admin.
    assert client.get(path, headers={"Authorization": f"Bearer {key}"}).status_code == 403
    assert client.post(path, json={"host": "a.com"}, headers={"Authorization": f"Bearer {key}"}).status_code == 403


def test_api_crud_and_audit(api):
    client, app, keys, state = api
    pid = keys["alpha"][0]
    _as_admin(state, pid)
    path = f"/api/v1/projects/{pid}/egress-hosts"
    r = client.post(path, json={"host": "Hooks.Example.COM."})
    assert r.status_code == 201 and r.json()["host"] == "hooks.example.com" and r.json()["created_by"] == "u1"
    assert client.post(path, json={"host": "hooks.example.com"}).status_code == 409
    assert client.post(path, json={"host": "*.svc.example.com"}).status_code == 201
    items = client.get(path).json()["items"]
    assert [i["host"] for i in items] == ["hooks.example.com", "*.svc.example.com"]
    assert set(items[0]) == {"id", "host", "created_at", "created_by"}
    assert client.delete(f"{path}/{items[0]['id']}").status_code == 204
    assert client.delete(f"{path}/{items[0]['id']}").status_code == 404
    assert [i["host"] for i in client.get(path).json()["items"]] == ["*.svc.example.com"]
    with app.state.session_factory() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.action.like("egress.host_%")).order_by(AuditLog.ts))
        got = [(r.action, r.target, r.project_id, r.actor_kind, r.actor_id) for r in rows]
    assert got == [
        ("egress.host_add", "hooks.example.com", pid, "user", "u1"),
        ("egress.host_add", "*.svc.example.com", pid, "user", "u1"),
        ("egress.host_remove", "hooks.example.com", pid, "user", "u1"),
    ]


def test_api_project_isolation(api):
    client, _app, keys, state = api
    alpha, beta = keys["alpha"][0], keys["beta"][0]
    _as_admin(state, beta)
    beta_host = client.post(f"/api/v1/projects/{beta}/egress-hosts", json={"host": "beta.com"}).json()
    _as_admin(state, alpha)
    base = f"/api/v1/projects/{beta}/egress-hosts"
    assert client.get(base).status_code == 404
    assert client.post(base, json={"host": "x.com"}).status_code == 404
    assert client.delete(f"{base}/{beta_host['id']}").status_code == 404
    # Another project's host id through the caller's own path is also not found.
    assert client.delete(f"/api/v1/projects/{alpha}/egress-hosts/{beta_host['id']}").status_code == 404
    assert client.get(f"/api/v1/projects/{alpha}/egress-hosts").json()["items"] == []


@pytest.mark.parametrize("host", ["*", "*.com", "*.co.uk", "127.0.0.1", "[::1]", "localhost", "a.*.example.com",
                                  "*example.com", "intranet", "http://example.com", "example.com/p", ""])
def test_api_rejects_invalid_hosts(api, host):
    client, _app, keys, state = api
    pid = keys["alpha"][0]
    _as_admin(state, pid)
    resp = client.post(f"/api/v1/projects/{pid}/egress-hosts", json={"host": host})
    assert resp.status_code == 422


# --- TLS: pinned IP, certificate verified against the original hostname -------------------------


@pytest.fixture
def tls_server(tmp_path):
    import shutil
    import ssl
    import subprocess

    if not shutil.which("openssl"):
        pytest.skip("openssl not available")
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=api.example.com",
         "-addext", "subjectAltName=DNS:api.example.com", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    srv = _QuietServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], ssl.create_default_context(cafile=str(cert))
    srv.shutdown()
    srv.server_close()


def test_tls_verifies_original_hostname_on_pinned_ip(db, tls_server):
    port, trust = tls_server
    net = _Net(port, {"api.example.com": [PUBLIC_IP], "other.example.com": [PUBLIC_IP]})
    client = _client(db, settings=_public())
    client._ssl_context = trust  # trust the test CA only
    resp = client.get("https://api.example.com/echo")
    assert resp.status_code == 200 and resp.json()["host"] == "api.example.com"
    with pytest.raises(EgressError) as exc:  # same IP, but the certificate is not for this name
        client.get("https://other.example.com/ok")
    assert exc.value.reason == "connection_error"
    assert net.dials == [PUBLIC_IP, PUBLIC_IP]
