"""SDK data-sets (L7) against an in-memory server that mimics `/api/v1/data-sets` and the artifact upload."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import httpx
import pytest

import matrix_sdk as mx
from matrix_sdk import data_sets


class FakeDataSetsServer:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.sets: dict[str, list[dict]] = {}
        self.uploads: list[str] = []
        self.redirect_downloads = False
        self.corrupt = False
        self.auth_seen: list[tuple[str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        m, path = request.method, request.url.path
        self.auth_seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "storage.test":
            return httpx.Response(200, content=b"tampered!" if self.corrupt else self.blobs[path.lstrip("/")])
        body = json.loads(request.content) if request.headers.get("content-type", "").startswith("application/json") else None
        if m == "POST" and path == "/api/v1/artifacts":
            sha = hashlib.sha256(request.content).hexdigest()
            self.blobs[sha] = request.content
            self.uploads.append(request.headers["x-filename"])
            return httpx.Response(201, json={"id": sha[:8], "sha256": sha, "size": len(request.content)})
        if m == "POST" and path == "/api/v1/data-sets/_files/missing":
            return httpx.Response(200, json={"missing": [s for s in sorted(set(body["sha256"])) if s not in self.blobs]})
        if m == "POST" and path == "/api/v1/data-sets":
            if body["name"] in self.sets:
                return httpx.Response(409, json={"detail": "data-set already exists"})
            self.sets[body["name"]] = []
            return httpx.Response(201, json={"name": body["name"], "kind": "files", "version_count": 0})
        mm = re.fullmatch(r"/api/v1/data-sets/([^/]+)/versions", path)
        if m == "POST" and mm:
            versions = self.sets.get(mm.group(1))
            if versions is None:
                return httpx.Response(404, json={"detail": "data-set not found"})
            files = sorted(body["files"], key=lambda f: f["path"])
            if versions and [(f["path"], f["sha256"]) for f in versions[-1]["files"]] == [(f["path"], f["sha256"]) for f in files]:
                return httpx.Response(200, json={k: v for k, v in versions[-1].items() if k != "files"})
            v = {"version": len(versions) + 1, "dataset": mm.group(1), "file_count": len(files),
                 "message": body.get("message"), "files": files}
            versions.append(v)
            return httpx.Response(201, json={k: x for k, x in v.items() if k != "files"})
        mm = re.fullmatch(r"/api/v1/data-sets/([^/]+)/versions/([^/]+)", path)
        if m == "GET" and mm:
            versions = self.sets[mm.group(1)]
            v = versions[-1] if mm.group(2) == "latest" else versions[int(mm.group(2)) - 1]
            items = [{**f, "artifact_id": None} for f in v["files"] if f["path"].startswith(request.url.params.get("prefix", ""))]
            return httpx.Response(200, json={"version": {k: x for k, x in v.items() if k != "files"},
                                             "files": {"items": items, "next_cursor": None}})
        mm = re.fullmatch(r"/api/v1/data-sets/([^/]+)/versions/([^/]+)/files/(.+)", path)
        if m == "GET" and mm:
            versions = self.sets[mm.group(1)]
            v = versions[-1] if mm.group(2) == "latest" else versions[int(mm.group(2)) - 1]
            f = next((f for f in v["files"] if f["path"] == mm.group(3)), None)
            if f is None:
                return httpx.Response(404, json={"detail": "file not found in this version"})
            if self.redirect_downloads:
                return httpx.Response(302, headers={"location": f"https://storage.test/{f['sha256']}", "x-content-sha256": f["sha256"]})
            data = b"tampered!" if self.corrupt else self.blobs[f["sha256"]]
            return httpx.Response(200, content=data, headers={"x-content-sha256": f["sha256"]})
        return httpx.Response(404, json={"detail": f"no route {m} {path}"})


@pytest.fixture
def server():
    return FakeDataSetsServer()


@pytest.fixture
def client(server):
    c = mx.MatrixClient("mx_k", "http://m.test", transport=httpx.MockTransport(server.handler))
    yield c
    c._http.close()


def _tree(root: Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha")
    (root / "sub" / "b.csv").write_text("x,y\n1,2\n")
    (root / "sub" / "copy.csv").write_text("x,y\n1,2\n")  # same content as b.csv


def test_create_and_upload_directory_dedupes_and_is_idempotent(client, server, tmp_path):
    src = tmp_path / "src"
    _tree(src)
    assert data_sets.create("corpus", "demo", client=client)["name"] == "corpus"
    v1 = data_sets.upload_version("corpus", src, message="first", client=client)
    assert v1["version"] == 1 and v1["created"] is True and v1["file_count"] == 3
    assert len(server.uploads) == 2  # b.csv and copy.csv share one blob: sent once
    got = data_sets.get_version("corpus", client=client)
    assert [f["path"] for f in got["files"]["items"]] == ["a.txt", "sub/b.csv", "sub/copy.csv"]
    assert got["files"]["items"][1]["media_type"] == "text/csv"
    again = data_sets.upload_version("corpus", src, client=client)
    assert again["created"] is False and again["version"] == 1 and len(server.uploads) == 2
    (src / "a.txt").write_text("alpha2")
    v2 = data_sets.upload_version("corpus", src, client=client)
    assert v2["version"] == 2 and v2["created"] and server.uploads[-1] == "a.txt" and len(server.uploads) == 3
    assert data_sets.get_version("corpus", 1, prefix="sub/", client=client)["files"]["items"][0]["path"] == "sub/b.csv"


def test_upload_files_named_relative_to_common_parent(client, tmp_path):
    (tmp_path / "x" / "y").mkdir(parents=True)
    f1, f2 = tmp_path / "x" / "one.txt", tmp_path / "x" / "y" / "two.txt"
    f1.write_text("1")
    f2.write_text("2")
    v = client.data_sets.upload_version("auto", [f1, f2])  # created on the fly
    assert v["file_count"] == 2
    assert [f["path"] for f in client.data_sets.get_version("auto")["files"]["items"]] == ["one.txt", "y/two.txt"]
    with pytest.raises(FileNotFoundError):
        client.data_sets.upload_version("auto", [tmp_path / "missing.txt"])
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        client.data_sets.upload_version("auto", empty)


def test_download_verifies_content_and_follows_redirect_without_credentials(client, server, tmp_path, monkeypatch):
    from matrix_sdk._net import client_kwargs

    monkeypatch.setattr(data_sets, "client_kwargs", lambda: client_kwargs(httpx.MockTransport(server.handler)))
    src = tmp_path / "src"
    _tree(src)
    data_sets.upload_version("corpus", src, client=client)
    out = data_sets.download("corpus", 1, "sub/b.csv", tmp_path / "out" / "b.csv", client=client)
    assert out.read_text() == "x,y\n1,2\n"
    server.redirect_downloads = True
    server.auth_seen.clear()
    out2 = data_sets.download("corpus", "latest", "a.txt", tmp_path / "a2.txt", client=client)
    assert out2.read_text() == "alpha"
    assert ("storage.test", None) in server.auth_seen  # the MATRIX key never reaches the storage host
    server.corrupt = True
    with pytest.raises(mx.MatrixError, match="sha256 mismatch"):
        data_sets.download("corpus", 1, "a.txt", tmp_path / "bad.txt", client=client)
    assert not (tmp_path / "bad.txt").exists() and not (tmp_path / "bad.txt.part").exists()
    server.corrupt = False
    with pytest.raises(mx.MatrixError, match="404"):
        data_sets.download("corpus", 1, "nope.txt", tmp_path / "n.txt", client=client)


def test_pull_and_errors(client, tmp_path):
    src = tmp_path / "src"
    _tree(src)
    client.data_sets.upload_version("corpus", src)
    files = client.data_sets.pull("corpus", 1, tmp_path / "dst")
    assert sorted(p.relative_to(tmp_path / "dst").as_posix() for p in files) == ["a.txt", "sub/b.csv", "sub/copy.csv"]
    with pytest.raises(mx.MatrixError, match="could not create the version"):
        client.data_sets.upload_version("ghost", src, create=False)
    with pytest.raises(mx.MatrixError):
        data_sets.create("corpus", client=client)  # 409 from the server
