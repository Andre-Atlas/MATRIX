"""File data-sets (L7, contract G3 section 2): `/api/v1/data-sets`."""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from rbac_helpers import api_key_headers, project_with_roles
from sqlalchemy import create_engine, select, text

from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.data_sets import service
from matrix_server.data_sets.models import DataSetFile, DataSetTag, DataSetVersion
from matrix_server.storage.models import AuditLog

PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    schema = None
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path}/ds.db"
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        schema = f"l7_{uuid.uuid4().hex[:10]}"  # private schema: parallel runs share the database
        admin = create_engine(PG_URL)
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        admin.dispose()
        sep = "&" if "?" in PG_URL else "?"
        url = f"{PG_URL}{sep}options=-csearch_path%3D{schema}"
    app = create_app(Settings(database_url=url, span_store="memory", artifact_local_dir=str(tmp_path / "a")))
    client = TestClient(app)
    pid, heads = project_with_roles(app.state.session_factory, "p1")
    _pid2, heads2 = project_with_roles(app.state.session_factory, "p2")
    try:
        yield client, app, pid, heads, heads2
    finally:
        app.state.db_engine.dispose()
        if schema:
            admin = create_engine(PG_URL)
            with admin.begin() as conn:
                conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            admin.dispose()


def _upload(client, h, data: bytes, name="f.bin", media="application/octet-stream") -> dict:
    resp = client.post(
        "/api/v1/artifacts", content=data,
        headers={**h, "x-filename": name, "content-type": media, "x-sha256": hashlib.sha256(data).hexdigest()},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _entry(art: dict, path: str) -> dict:
    return {"path": path, "artifact_id": art["id"], "size": art["size"]}


def _mk(client, h, name="corpus") -> dict:
    resp = client.post("/api/v1/data-sets", json={"name": name, "description": "d"}, headers=h)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _version(client, h, files, name="corpus", **kw):
    return client.post(f"/api/v1/data-sets/{name}/versions", json={"files": files, **kw}, headers=h)


def test_create_list_get_and_duplicates(env):
    client, _app, _pid, heads, _ = env
    m = heads["member"]
    ds = _mk(client, m)
    assert ds["name"] == "corpus" and ds["kind"] == "files" and ds["version_count"] == 0
    assert client.post("/api/v1/data-sets", json={"name": "corpus"}, headers=m).status_code == 409
    for bad in ("", "../x", "a b", "-x", "a/b", "x" * 129):
        assert client.post("/api/v1/data-sets", json={"name": bad}, headers=m).status_code == 422, bad
    assert client.post("/api/v1/data-sets", json={"name": "g", "kind": "golden"}, headers=m).status_code == 422
    _mk(client, m, "alpha")
    page = client.get("/api/v1/data-sets?limit=1", headers=heads["viewer"]).json()
    assert [i["name"] for i in page["items"]] == ["alpha"] and page["next_cursor"]
    page2 = client.get(f"/api/v1/data-sets?limit=1&cursor={page['next_cursor']}", headers=heads["viewer"]).json()
    assert [i["name"] for i in page2["items"]] == ["corpus"] and page2["next_cursor"] is None
    assert client.get("/api/v1/data-sets/corpus", headers=heads["viewer"]).json()["name"] == "corpus"
    assert client.get("/api/v1/data-sets/nope", headers=heads["viewer"]).status_code == 404


def test_version_manifest_hash_and_idempotency(env):
    client, _app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    a, b = _upload(client, m, b"aaa", "a.txt"), _upload(client, m, b"bbbb", "b.txt")
    resp = _version(client, m, [_entry(b, "dir/b.txt"), _entry(a, "a.txt")], message="first")
    assert resp.status_code == 201, resp.text
    v1 = resp.json()
    assert v1["version"] == 1 and v1["file_count"] == 2 and v1["total_bytes"] == 7
    assert v1["parent_version"] is None and v1["source"] == {"type": "upload", "ref": None}
    assert v1["tags"] == ["latest"] and v1["dataset"] == "corpus"
    sha_a, sha_b = hashlib.sha256(b"aaa").hexdigest(), hashlib.sha256(b"bbbb").hexdigest()
    canonical = f'[{{"path":"a.txt","sha256":"{sha_a}","size":3}},{{"path":"dir/b.txt","sha256":"{sha_b}","size":4}}]'
    expected = hashlib.sha256(canonical.encode()).hexdigest()
    assert v1["manifest_sha256"] == expected
    # Same manifest (other order, sha256 instead of artifact id) -> same version, 200.
    again = _version(client, m, [
        {"path": "a.txt", "sha256": a["sha256"]}, {"path": "dir/b.txt", "sha256": b["sha256"], "size": 4}])
    assert again.status_code == 200 and again.json()["id"] == v1["id"]
    # A change creates v2 with v1 as parent; both versions stay immutable.
    c = _upload(client, m, b"cc", "c.txt")
    v2 = _version(client, m, [_entry(a, "a.txt"), _entry(b, "dir/b.txt"), _entry(c, "c.txt")]).json()
    assert v2["version"] == 2 and v2["parent_version"] == 1 and v2["tags"] == ["latest"]
    got1 = client.get("/api/v1/data-sets/corpus/versions/1", headers=heads["viewer"]).json()
    assert got1["version"]["tags"] == [] and got1["version"]["file_count"] == 2
    assert [f["path"] for f in got1["files"]["items"]] == ["a.txt", "dir/b.txt"]
    assert got1["files"]["items"][0]["media_type"] == "application/octet-stream"
    ds = client.get("/api/v1/data-sets/corpus", headers=heads["viewer"]).json()
    assert ds["version_count"] == 2 and ds["latest_version"]["version"] == 2
    # Reverting to the v1 manifest is a new version (idempotency is against the latest only).
    v3 = _version(client, m, [_entry(a, "a.txt"), _entry(b, "dir/b.txt")], parent_version=1).json()
    assert v3["version"] == 3 and v3["manifest_sha256"] == v1["manifest_sha256"] and v3["parent_version"] == 1
    listing = client.get("/api/v1/data-sets/corpus/versions?limit=2", headers=heads["viewer"]).json()
    assert [v["version"] for v in listing["items"]] == [3, 2] and listing["next_cursor"] == 2
    rest = client.get(f"/api/v1/data-sets/corpus/versions?cursor={listing['next_cursor']}", headers=heads["viewer"]).json()
    assert [v["version"] for v in rest["items"]] == [1] and rest["next_cursor"] is None
    assert _version(client, m, [_entry(a, "a.txt")], parent_version=9).status_code == 422


def test_path_and_manifest_validation(env):
    client, _app, _pid, heads, heads2 = env
    m = heads["member"]
    _mk(client, m)
    a = _upload(client, m, b"x")
    other = _upload(client, heads2["member"], b"x")
    for path in ("../a", "a/../b", "/abs", "a//b", "./a", "a/", "a\\b", "C:/x", "a/./b", "", "x" * 1025, "a\x00b", "a/" + "s" * 256):
        assert _version(client, m, [{"path": path, "artifact_id": a["id"]}]).status_code == 422, path
    assert _version(client, m, [_entry(a, "d"), _entry(a, "d")]).status_code == 422  # duplicate
    assert _version(client, m, [_entry(a, "d"), _entry(a, "d/e")]).status_code == 422  # file vs directory
    assert _version(client, m, []).status_code == 422
    assert _version(client, m, [{"path": "x"}]).status_code == 422  # no reference
    assert _version(client, m, [{"path": "x", "artifact_id": a["id"], "size": 99}]).status_code == 422
    assert _version(client, m, [{"path": "x", "artifact_id": a["id"], "sha256": "0" * 64}]).status_code == 422
    assert _version(client, m, [{"path": "x", "sha256": "0" * 64}]).status_code == 422
    assert _version(client, m, [{"path": "x", "artifact_id": "nope"}]).status_code == 422
    # Artifacts of another project are invisible, by id and by digest.
    assert _version(client, m, [{"path": "x", "artifact_id": other["id"]}]).status_code == 422
    only_other = _upload(client, heads2["member"], b"only-in-p2")
    assert _version(client, m, [{"path": "x", "sha256": only_other["sha256"]}]).status_code == 422
    # A unicode path is NFC-normalized.
    resp = _version(client, m, [{"path": "cafe\u0301.txt", "artifact_id": a["id"]}])
    assert resp.status_code == 201
    files = client.get("/api/v1/data-sets/corpus/versions/1", headers=m).json()["files"]["items"]
    assert files[0]["path"] == "caf\u00e9.txt"
    assert client.get("/api/v1/data-sets/corpus/versions/1/files/nope", headers=m).status_code == 404


def test_file_limit_is_configurable(env):
    client, app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    arts = [_upload(client, m, bytes([i]) * 3) for i in range(4)]
    app.state.settings.__dict__["data_set_max_files"] = 3  # pydantic model: bypass for the test
    try:
        assert service.max_files(app.state.settings) == 3
        assert _version(client, m, [_entry(a, f"f{i}") for i, a in enumerate(arts)]).status_code == 422
        assert _version(client, m, [_entry(a, f"f{i}") for i, a in enumerate(arts[:3])]).status_code == 201
    finally:
        app.state.settings.__dict__.pop("data_set_max_files", None)
    assert service.max_files(app.state.settings) == service.DEFAULT_MAX_FILES_PER_VERSION == 100_000


def test_missing_files_check(env):
    client, _app, _pid, heads, _ = env
    m = heads["member"]
    a = _upload(client, m, b"have")
    absent = hashlib.sha256(b"absent").hexdigest()
    resp = client.post("/api/v1/data-sets/_files/missing", json={"sha256": [a["sha256"], absent, absent.upper()]}, headers=m)
    assert resp.status_code == 200 and resp.json() == {"missing": [absent]}
    assert client.post("/api/v1/data-sets/_files/missing", json={"sha256": ["zz"]}, headers=m).status_code in (200, 422)


def test_diff_added_removed_modified_paginated(env):
    client, _app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    v1_files = {f"f{i:02d}.txt": f"v1-{i}".encode() for i in range(6)}
    arts = {p: _upload(client, m, d) for p, d in v1_files.items()}
    _version(client, m, [_entry(a, p) for p, a in arts.items()])
    new = {p: a for p, a in arts.items() if p not in ("f00.txt", "f01.txt")}  # removed f00, f01
    new["f02.txt"] = _upload(client, m, b"changed-2")  # modified
    new["f03.txt"] = _upload(client, m, b"changed-3")  # modified
    new["g.txt"] = _upload(client, m, b"new")  # added
    _version(client, m, [_entry(a, p) for p, a in new.items()])
    d = client.get("/api/v1/data-sets/corpus/versions/1/diff/2", headers=heads["viewer"]).json()
    assert [x["path"] for x in d["added"]] == ["g.txt"]
    assert [x["path"] for x in d["removed"]] == ["f00.txt", "f01.txt"]
    assert [x["path"] for x in d["modified"]] == ["f02.txt", "f03.txt"]
    assert d["modified"][0]["from"]["sha256"] == arts["f02.txt"]["sha256"]
    assert d["modified"][0]["to"]["sha256"] == new["f02.txt"]["sha256"]
    assert d["unchanged_count"] == 2 and d["counts"] == {"added": 1, "removed": 2, "modified": 2}
    assert d["next_cursor"] is None and (d["from_version"], d["to_version"]) == (1, 2)
    # Pagination walks every change exactly once, ordered by path.
    seen, cursor = [], None
    while True:
        url = "/api/v1/data-sets/corpus/versions/1/diff/latest?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=heads["viewer"]).json()
        seen += [x["path"] for k in ("added", "removed", "modified") for x in page[k]]
        assert page["unchanged_count"] == 2 and page["counts"]["removed"] == 2
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == seen == ["f00.txt", "f01.txt", "f02.txt", "f03.txt", "g.txt"]
    only = client.get("/api/v1/data-sets/corpus/versions/1/diff/2?kind=removed", headers=heads["viewer"]).json()
    assert [x["path"] for x in only["removed"]] == ["f00.txt", "f01.txt"] and only["added"] == [] and only["modified"] == []
    same = client.get("/api/v1/data-sets/corpus/versions/2/diff/2", headers=heads["viewer"]).json()
    assert same["added"] == same["removed"] == same["modified"] == [] and same["unchanged_count"] == 5
    assert client.get("/api/v1/data-sets/corpus/versions/1/diff/9", headers=heads["viewer"]).status_code == 404


def test_file_listing_pagination_and_prefix(env):
    client, _app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    arts = [_upload(client, m, f"d{i}".encode()) for i in range(5)]
    paths = ["a/1.txt", "a/2.txt", "a_b/3.txt", "b/4.txt", "root.txt"]
    _version(client, m, [_entry(a, p) for a, p in zip(arts, paths)])
    page = client.get("/api/v1/data-sets/corpus/versions/1?limit=2", headers=m).json()["files"]
    assert [f["path"] for f in page["items"]] == paths[:2] and page["next_cursor"]
    nxt = client.get(f"/api/v1/data-sets/corpus/versions/1?limit=10&cursor={page['next_cursor']}", headers=m).json()["files"]
    assert [f["path"] for f in nxt["items"]] == paths[2:] and nxt["next_cursor"] is None
    pre = client.get("/api/v1/data-sets/corpus/versions/1?prefix=a/", headers=m).json()["files"]["items"]
    assert [f["path"] for f in pre] == ["a/1.txt", "a/2.txt"]  # '_' is not a LIKE wildcard
    assert client.get("/api/v1/data-sets/corpus/versions/1?cursor=@@@", headers=m).status_code == 400


def test_tags_and_latest_alias(env):
    client, _app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    a, b = _upload(client, m, b"1"), _upload(client, m, b"22")
    _version(client, m, [_entry(a, "x")])
    _version(client, m, [_entry(a, "x"), _entry(b, "y")])
    assert client.put("/api/v1/data-sets/corpus/tags/latest", json={"version": 1}, headers=m).status_code == 422
    assert client.put("/api/v1/data-sets/corpus/tags/1", json={"version": 1}, headers=m).status_code == 422
    assert client.put("/api/v1/data-sets/corpus/tags/prod", json={"version": 7}, headers=m).status_code == 404
    first = client.put("/api/v1/data-sets/corpus/tags/prod", json={"version": 1}, headers=m)
    assert first.status_code == 200 and first.json()["previous_version"] is None
    moved = client.put("/api/v1/data-sets/corpus/tags/prod", json={"version": 2}, headers=m).json()
    assert moved["previous_version"] == 1
    client.put("/api/v1/data-sets/corpus/tags/gold", json={"version": 1}, headers=m)
    v = heads["viewer"]
    assert client.get("/api/v1/data-sets/corpus/versions/prod", headers=v).json()["version"]["version"] == 2
    assert client.get("/api/v1/data-sets/corpus/versions/latest", headers=v).json()["version"]["version"] == 2
    assert client.get("/api/v1/data-sets/corpus/versions/v1", headers=v).json()["version"]["tags"] == ["gold"]
    assert client.get("/api/v1/data-sets/corpus/versions/2", headers=v).json()["version"]["tags"] == ["latest", "prod"]
    tags = {t["tag"]: t["version"] for t in client.get("/api/v1/data-sets/corpus/tags", headers=v).json()["items"]}
    assert tags == {"gold": 1, "prod": 2, "latest": 2}
    assert client.get("/api/v1/data-sets/corpus/versions/ghost", headers=v).status_code == 404
    assert client.delete("/api/v1/data-sets/corpus/tags/prod", headers=m).status_code == 204
    assert client.delete("/api/v1/data-sets/corpus/tags/prod", headers=m).status_code == 404
    assert client.delete("/api/v1/data-sets/corpus/tags/latest", headers=m).status_code == 404
    assert client.get("/api/v1/data-sets/corpus/versions/prod", headers=v).status_code == 404
    diff = client.get("/api/v1/data-sets/corpus/versions/gold/diff/latest", headers=v).json()
    assert [x["path"] for x in diff["added"]] == ["y"]


def test_download_file_protections(env):
    client, _app, _pid, heads, heads2 = env
    m, v = heads["member"], heads["viewer"]
    _mk(client, m)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    page = b"<html><script>alert(1)</script></html>"
    a_png = _upload(client, m, png, "pic.png", "image/png")
    a_html = _upload(client, m, page, "p.html", "text/html")
    _version(client, m, [_entry(a_png, "imgs/pic.png"), _entry(a_html, "web/p.html")])
    resp = client.get("/api/v1/data-sets/corpus/versions/1/files/web/p.html?inline=true", headers=v)
    assert resp.status_code == 200 and resp.content == page
    assert resp.headers["content-disposition"].startswith("attachment") and "p.html" in resp.headers["content-disposition"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-security-policy"] == "sandbox"
    assert resp.headers["cache-control"] == "private, no-store"
    img = client.get("/api/v1/data-sets/corpus/versions/latest/files/imgs/pic.png?inline=true", headers=v)
    assert img.status_code == 200 and img.content == png and img.headers["content-disposition"].startswith("inline")
    assert client.get("/api/v1/data-sets/corpus/versions/1/files/imgs/pic.png", headers=v).headers[
        "content-disposition"].startswith("attachment")
    # No credential, other project: refused / not found.
    assert client.get("/api/v1/data-sets/corpus/versions/1/files/web/p.html").status_code == 401
    assert client.get("/api/v1/data-sets/corpus/versions/1/files/web/p.html", headers=heads2["viewer"]).status_code == 404


def test_rbac_and_cross_project_isolation(env):
    client, app, pid, heads, heads2 = env
    m, v, adm = heads["member"], heads["viewer"], heads["admin"]
    assert client.post("/api/v1/data-sets", json={"name": "corpus"}, headers=v).status_code == 403
    _mk(client, m)
    a = _upload(client, m, b"x")
    assert _version(client, v, [_entry(a, "x")]).status_code == 403
    _version(client, m, [_entry(a, "x")])
    assert client.put("/api/v1/data-sets/corpus/tags/t", json={"version": 1}, headers=v).status_code == 403
    assert client.delete("/api/v1/data-sets/corpus/tags/t", headers=v).status_code == 403
    assert client.post("/api/v1/data-sets/_files/missing", json={"sha256": [a["sha256"]]}, headers=v).status_code == 403
    for path in ("", "/versions", "/versions/1", "/tags", "/versions/1/files/x"):
        assert client.get(f"/api/v1/data-sets/corpus{path}", headers=v).status_code == 200, path
    # Delete needs ADMIN: neither a member nor a project API key.
    assert client.delete("/api/v1/data-sets/corpus", headers=m).status_code == 403
    assert client.delete("/api/v1/data-sets/corpus", headers=api_key_headers(app.state.session_factory, pid)).status_code == 403
    # Other project: nothing is visible and the same name is free.
    other = heads2["admin"]
    assert client.get("/api/v1/data-sets", headers=other).json()["items"] == []
    assert client.get("/api/v1/data-sets/corpus", headers=other).status_code == 404
    assert client.get("/api/v1/data-sets/corpus/versions/1", headers=other).status_code == 404
    assert client.delete("/api/v1/data-sets/corpus", headers=other).status_code == 404
    assert client.post("/api/v1/data-sets", json={"name": "corpus"}, headers=heads2["member"]).status_code == 201
    assert client.delete("/api/v1/data-sets/corpus", headers=adm).status_code == 204
    assert client.get("/api/v1/data-sets/corpus", headers=other).status_code == 200


def test_audit_and_delete_cascade(env):
    client, app, pid, heads, _ = env
    m, adm = heads["member"], heads["admin"]
    _mk(client, m)
    a = _upload(client, m, b"x")
    _version(client, m, [_entry(a, "x")])
    _version(client, m, [_entry(a, "x")])  # idempotent: no second audit row
    client.put("/api/v1/data-sets/corpus/tags/t", json={"version": 1}, headers=m)
    client.delete("/api/v1/data-sets/corpus/tags/t", headers=m)
    assert client.delete("/api/v1/data-sets/corpus", headers=adm).status_code == 204
    with app.state.session_factory() as s:
        actions = [r.action for r in s.scalars(select(AuditLog).where(AuditLog.project_id == pid).order_by(AuditLog.ts))
                   if r.action.startswith("data_set.")]
        assert actions.count("data_set.version.create") == 1
        assert {"data_set.create", "data_set.delete", "data_set.tag.set", "data_set.tag.delete"} <= set(actions)
        for model in (DataSetVersion, DataSetFile, DataSetTag):
            assert s.scalar(select(model).limit(1)) is None
    assert client.get("/api/v1/data-sets/corpus", headers=adm).status_code == 404
    # The artifact itself is untouched.
    assert client.get(f"/api/v1/artifacts/{a['id']}", headers=m).status_code == 200


def test_artifact_in_a_version_cannot_be_deleted(env):
    client, _app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    a = _upload(client, m, b"shared")
    twin = _upload(client, m, b"shared", "twin.bin")  # same content, another row
    other = _upload(client, m, b"unrelated")
    _version(client, m, [_entry(a, "x")])
    assert client.delete(f"/api/v1/artifacts/{other['id']}", headers=m).status_code == 204
    # Another row still holds the blob: deletable. The last one is not.
    assert client.delete(f"/api/v1/artifacts/{twin['id']}", headers=m).status_code == 204
    resp = client.delete(f"/api/v1/artifacts/{a['id']}", headers=m)
    assert resp.status_code == 409 and "data-set" in resp.json()["detail"]
    assert client.get("/api/v1/data-sets/corpus/versions/1/files/x", headers=m).content == b"shared"
    # Once the data-set is gone the artifact can be deleted.
    assert client.delete("/api/v1/data-sets/corpus", headers=heads["admin"]).status_code == 204
    assert client.delete(f"/api/v1/artifacts/{a['id']}", headers=m).status_code == 204


def test_quota_is_respected_when_uploading_and_manifests_add_no_bytes(env):
    client, app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    app.state.settings.__dict__["project_quota_bytes"] = 10
    try:
        a = _upload(client, m, b"123456")
        over = client.post("/api/v1/artifacts", content=b"abcdefgh", headers={**m, "x-filename": "big"})
        assert over.status_code == 413  # the artifact store enforces the quota before any manifest
        # Referencing the stored content in several versions and paths costs nothing.
        assert _version(client, m, [_entry(a, "one"), _entry(a, "two")]).status_code == 201
        assert _version(client, m, [_entry(a, "one")]).status_code == 201
    finally:
        app.state.settings.__dict__.pop("project_quota_bytes", None)


def test_runs_reference_data_set_versions_and_lineage(env):
    client, _app, _pid, heads, heads2 = env
    m, v = heads["member"], heads["viewer"]
    _mk(client, m)
    a = _upload(client, m, b"x")
    ver = _version(client, m, [_entry(a, "x")]).json()
    foreign_ds = client.post("/api/v1/data-sets", json={"name": "theirs"}, headers=heads2["member"]).json()
    fa = _upload(client, heads2["member"], b"y")
    fver = _version(client, heads2["member"], [_entry(fa, "y")], name="theirs").json()
    assert foreign_ds["name"] == "theirs"
    # Foreign and unknown references are refused; the own version is accepted.
    assert client.post("/api/v1/runs", json={"dataset_version_id": fver["id"]}, headers=m).status_code == 422
    assert client.post("/api/v1/runs", json={"dataset_version_id": uuid.uuid4().hex}, headers=m).status_code == 422
    run = client.post("/api/v1/runs", json={"name": "r1", "dataset_version_id": ver["id"]}, headers=m)
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    assert run.json()["dataset_version_id"] == ver["id"]
    # Bounded lineage: from the run (up) and from the version (down).
    up = client.get(f"/api/v1/lineage?node=run:{run_id}&direction=up", headers=v).json()
    node = next(n for n in up["nodes"] if n["kind"] == "data_set_version")
    assert node["entity_id"] == ver["id"] and node["label"] == "corpus v1"
    assert node["meta"]["file_count"] == 1 and node["meta"]["data_set"] == "corpus"
    assert any(e["from"] == f"data_set_version:{ver['id']}" and e["to"] == f"run:{run_id}" for e in up["edges"])
    down = client.get(f"/api/v1/lineage?node=data_set_version:{ver['id']}&direction=down", headers=v)
    assert down.status_code == 200, down.text
    assert {n["id"] for n in down.json()["nodes"]} == {f"data_set_version:{ver['id']}", f"run:{run_id}"}
    # Another project cannot see the node.
    assert client.get(f"/api/v1/lineage?node=data_set_version:{ver['id']}", headers=heads2["viewer"]).status_code == 404
    # Deleting a referenced data-set needs force.
    assert client.delete("/api/v1/data-sets/corpus", headers=heads["admin"]).status_code == 409
    assert client.delete("/api/v1/data-sets/corpus?force=true", headers=heads["admin"]).status_code == 204


def test_concurrent_identical_versions_converge(env):
    import concurrent.futures

    client, _app, _pid, heads, _ = env
    m = heads["member"]
    _mk(client, m)
    a = _upload(client, m, b"x")
    files = [_entry(a, "x")]
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        codes = [r.status_code for r in pool.map(lambda _i: _version(client, m, files), range(4))]
    assert set(codes) <= {200, 201, 409} and 201 in codes
    listing = client.get("/api/v1/data-sets/corpus/versions", headers=m).json()["items"]
    assert [x["version"] for x in listing] == [1]


def test_normalize_path_unit():
    assert service.normalize_path("a/b.txt") == "a/b.txt"
    with pytest.raises(service.InvalidInput):
        service.normalize_path("a/../b")
