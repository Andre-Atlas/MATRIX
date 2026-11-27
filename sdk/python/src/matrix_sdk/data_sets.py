"""File data-sets (contract G3 section 2): versioned manifests of files over the artifact store.

Module-level helpers (`create`, `upload_version`, `get_version`, `download`, ...) use the client
given as `client=`, the one set by `matrix_sdk.init()` or `MATRIX_API_KEY`; `MatrixClient.data_sets`
exposes the same operations on `DataSetsClient`. Not to be confused with `MatrixClient.datasets`
(evaluation items).

`upload_version` hashes the files locally, asks the server which contents it does not have yet,
uploads only those (as artifacts, streamed) and creates the version from the manifest; a manifest
identical to the latest version's returns that version (`created` is False).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx

from ._net import client_kwargs
from .client import MatrixError, _seg
from .tracking import _resolve_client

if TYPE_CHECKING:
    from .client import MatrixClient

_CHUNK = 8 * 1024 * 1024
_TRANSFER_TIMEOUT = httpx.Timeout(30.0, read=600.0, write=600.0)
_MISSING_BATCH = 5000

__all__ = [
    "DataSetsClient",
    "create",
    "delete",
    "delete_tag",
    "diff",
    "download",
    "get",
    "get_version",
    "list_files",
    "list_versions",
    "pull",
    "set_tag",
    "upload_version",
]


def _fail(what: str, resp: httpx.Response) -> MatrixError:
    try:
        detail = resp.json().get("detail", resp.text[:200])
    except Exception:  # noqa: BLE001
        detail = resp.text[:200]
    return MatrixError(f"{what}: HTTP {resp.status_code} {detail}")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _collect(source: str | os.PathLike[str] | Sequence[str | os.PathLike[str]]) -> list[tuple[str, Path]]:
    """`(data-set path, local file)` pairs. A directory is walked and its files are named relative
    to it; files (one or several) are named relative to their common parent directory."""
    if isinstance(source, (str, os.PathLike)):
        paths = [Path(source)]
        single_dir = paths[0].is_dir()
    else:
        paths = [Path(p) for p in source]
        single_dir = False
    if not paths:
        raise ValueError("nothing to upload: no paths given")
    out: list[tuple[str, Path]] = []
    if single_dir:
        root = paths[0]
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fname in sorted(filenames):
                full = Path(dirpath) / fname
                if full.is_file():
                    out.append((PurePosixPath(*full.relative_to(root).parts).as_posix(), full))
    else:
        for p in paths:
            if not p.is_file():
                raise FileNotFoundError(f"not a file: {p}")
        base = Path(os.path.commonpath([str(p.resolve().parent) for p in paths]))
        for p in paths:
            out.append((PurePosixPath(*p.resolve().relative_to(base).parts).as_posix(), p))
    if not out:
        raise ValueError("nothing to upload: the directory has no files")
    return out


class DataSetsClient:
    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    # -- data-sets
    def create(self, name: str, description: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        return self._client._request("POST", "/api/v1/data-sets", json=body)

    def get(self, name: str) -> dict[str, Any]:
        return self._client._request("GET", f"/api/v1/data-sets/{_seg(name)}")

    def list(self, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._client._request("GET", "/api/v1/data-sets", params=params)

    def delete(self, name: str, *, force: bool = False) -> None:
        self._client._request("DELETE", f"/api/v1/data-sets/{_seg(name)}", params={"force": force})

    # -- versions
    def list_versions(self, name: str, limit: int = 50, cursor: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._client._request("GET", f"/api/v1/data-sets/{_seg(name)}/versions", params=params)

    def get_version(
        self,
        name: str,
        version: int | str = "latest",
        *,
        limit: int = 200,
        cursor: str | None = None,
        prefix: str | None = None,
    ) -> dict[str, Any]:
        """`{version, files: {items, next_cursor}}`; `version` is a number, `latest` or a tag."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if prefix:
            params["prefix"] = prefix
        return self._client._request(
            "GET", f"/api/v1/data-sets/{_seg(name)}/versions/{_seg(version)}", params=params
        )

    def list_files(self, name: str, version: int | str = "latest", *, prefix: str | None = None) -> Iterator[dict[str, Any]]:
        cursor = None
        while True:
            page = self.get_version(name, version, limit=1000, cursor=cursor, prefix=prefix)
            yield from page["files"]["items"]
            cursor = page["files"]["next_cursor"]
            if not cursor:
                return

    def diff(
        self, name: str, a: int | str, b: int | str, *, limit: int = 200, cursor: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if kind:
            params["kind"] = kind
        return self._client._request(
            "GET", f"/api/v1/data-sets/{_seg(name)}/versions/{_seg(a)}/diff/{_seg(b)}", params=params
        )

    def set_tag(self, name: str, tag: str, version: int) -> dict[str, Any]:
        return self._client._request("PUT", f"/api/v1/data-sets/{_seg(name)}/tags/{_seg(tag)}", json={"version": version})

    def delete_tag(self, name: str, tag: str) -> None:
        self._client._request("DELETE", f"/api/v1/data-sets/{_seg(name)}/tags/{_seg(tag)}")

    # -- upload
    def upload_version(
        self,
        name: str,
        source: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
        *,
        message: str | None = None,
        parent_version: int | None = None,
        create: bool = True,
    ) -> dict[str, Any]:
        """Create a version from a directory (files named relative to it) or from one or more
        files (named relative to their common parent). Returns the version, plus `created`."""
        files = _collect(source)
        http = self._client._http
        if create:
            resp = http.post("/api/v1/data-sets", json={"name": name})
            if resp.status_code not in (201, 409):
                raise _fail("could not create data-set", resp)
        entries: list[dict[str, Any]] = []
        local: dict[str, Path] = {}
        for rel, path in files:
            sha, size = _sha256_file(path)
            media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            entries.append({"path": rel, "sha256": sha, "size": size, "media_type": media})
            local.setdefault(sha, path)
        shas = sorted(local)
        missing: list[str] = []
        for i in range(0, len(shas), _MISSING_BATCH):
            resp = http.post("/api/v1/data-sets/_files/missing", json={"sha256": shas[i : i + _MISSING_BATCH]})
            if resp.status_code != 200:
                raise _fail("could not check the store", resp)
            missing += resp.json()["missing"]
        for sha in missing:
            self._upload_blob(local[sha], sha)
        body: dict[str, Any] = {"files": entries}
        if message is not None:
            body["message"] = message
        if parent_version is not None:
            body["parent_version"] = parent_version
        resp = http.post(f"/api/v1/data-sets/{_seg(name)}/versions", json=body)
        if resp.status_code not in (200, 201):
            raise _fail("could not create the version", resp)
        return {**resp.json(), "created": resp.status_code == 201}

    def _upload_blob(self, path: Path, sha: str) -> None:
        def chunks() -> Iterator[bytes]:
            with path.open("rb") as fh:
                yield from iter(lambda: fh.read(_CHUNK), b"")

        headers = {
            "X-Filename": path.name,
            "X-Sha256": sha,
            "Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "X-Owner-Kind": "data_set",
            "Content-Length": str(path.stat().st_size),
        }
        resp = self._client._http.post("/api/v1/artifacts", content=chunks(), headers=headers, timeout=_TRANSFER_TIMEOUT)
        if resp.status_code != 201:
            raise _fail(f"could not upload {path.name}", resp)

    # -- download
    def download(
        self, name: str, version: int | str, path: str, dest: str | os.PathLike[str] | None = None
    ) -> Path:
        """Download one file of a version, verify its sha256 and return the local path (`dest`, or
        the file's name in the current directory). A presigned redirect is followed without
        sending the MATRIX credential to the storage host."""
        target = Path(dest) if dest is not None else Path(PurePosixPath(path).name)
        url = f"/api/v1/data-sets/{_seg(name)}/versions/{_seg(version)}/files/{_seg(path).replace('%2F', '/')}"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        hasher = hashlib.sha256()

        def stream_into(http: httpx.Client, request_url: str) -> str | None:
            with http.stream("GET", request_url, timeout=_TRANSFER_TIMEOUT) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    resp.read()
                    expected[0] = expected[0] or resp.headers.get("x-content-sha256")
                    return resp.headers["location"]
                if resp.status_code != 200:
                    resp.read()
                    raise _fail(f"could not download {path}", resp)
                expected[0] = expected[0] or resp.headers.get("x-content-sha256")
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(_CHUNK):
                        hasher.update(chunk)
                        fh.write(chunk)
            return None

        expected: list[str | None] = [None]
        try:
            redirect = stream_into(self._client._http, url)
            if redirect is not None:
                with httpx.Client(**client_kwargs()) as external:
                    stream_into(external, redirect)
            got = hasher.hexdigest()
            if expected[0] and got != expected[0].lower():
                raise MatrixError(f"sha256 mismatch for {path}: expected {expected[0]}, got {got}")
            os.replace(tmp, target)
            return target
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def pull(self, name: str, version: int | str, directory: str | os.PathLike[str]) -> list[Path]:
        """Download every file of a version into `directory`, keeping the data-set paths."""
        root = Path(directory).resolve()
        out: list[Path] = []
        for entry in self.list_files(name, version):
            target = (root / entry["path"]).resolve()
            if root not in target.parents:  # the server validates paths; defence in depth
                raise MatrixError(f"refusing to write outside {root}: {entry['path']!r}")
            out.append(self.download(name, version, entry["path"], target))
        return out


# --- module-level helpers ---------------------------------------------------------------------------


def _with_client(client: MatrixClient | None):
    http_client, owns = _resolve_client(client)
    return DataSetsClient(http_client), http_client, owns


def _call(method: str, client: MatrixClient | None, *args: Any, **kwargs: Any) -> Any:
    api, http_client, owns = _with_client(client)
    try:
        return getattr(api, method)(*args, **kwargs)
    except httpx.HTTPStatusError as exc:
        raise MatrixError(f"data-sets {method}: HTTP {exc.response.status_code} {exc.response.text[:200]}") from None
    except httpx.HTTPError as exc:
        raise MatrixError(f"data-sets {method}: {exc}") from None
    finally:
        if owns:
            http_client._http.close()


def create(name: str, description: str | None = None, *, client: MatrixClient | None = None) -> dict[str, Any]:
    """Create a file data-set (`POST /api/v1/data-sets`)."""
    return _call("create", client, name, description)


def get(name: str, *, client: MatrixClient | None = None) -> dict[str, Any]:
    return _call("get", client, name)


def delete(name: str, *, force: bool = False, client: MatrixClient | None = None) -> None:
    _call("delete", client, name, force=force)


def upload_version(
    name: str,
    source: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
    *,
    message: str | None = None,
    parent_version: int | None = None,
    create: bool = True,
    client: MatrixClient | None = None,
) -> dict[str, Any]:
    """Create a version from a directory or from files; see `DataSetsClient.upload_version`."""
    return _call("upload_version", client, name, source, message=message, parent_version=parent_version, create=create)


def get_version(
    name: str,
    version: int | str = "latest",
    *,
    limit: int = 200,
    cursor: str | None = None,
    prefix: str | None = None,
    client: MatrixClient | None = None,
) -> dict[str, Any]:
    return _call("get_version", client, name, version, limit=limit, cursor=cursor, prefix=prefix)


def list_versions(name: str, *, limit: int = 50, cursor: int | None = None, client: MatrixClient | None = None) -> dict[str, Any]:
    return _call("list_versions", client, name, limit=limit, cursor=cursor)


def list_files(name: str, version: int | str = "latest", *, prefix: str | None = None, client: MatrixClient | None = None) -> list[dict[str, Any]]:
    api, http_client, owns = _with_client(client)
    try:
        return list(api.list_files(name, version, prefix=prefix))
    finally:
        if owns:
            http_client._http.close()


def diff(name: str, a: int | str, b: int | str, *, limit: int = 200, cursor: str | None = None, client: MatrixClient | None = None) -> dict[str, Any]:
    return _call("diff", client, name, a, b, limit=limit, cursor=cursor)


def set_tag(name: str, tag: str, version: int, *, client: MatrixClient | None = None) -> dict[str, Any]:
    return _call("set_tag", client, name, tag, version)


def delete_tag(name: str, tag: str, *, client: MatrixClient | None = None) -> None:
    _call("delete_tag", client, name, tag)


def download(
    name: str,
    version: int | str,
    path: str,
    dest: str | os.PathLike[str] | None = None,
    *,
    client: MatrixClient | None = None,
) -> Path:
    """Download one file of a version (verified by sha256) and return its local path."""
    return _call("download", client, name, version, path, dest)


def pull(name: str, version: int | str, directory: str | os.PathLike[str], *, client: MatrixClient | None = None) -> list[Path]:
    """Download every file of a version into `directory`."""
    return _call("pull", client, name, version, directory)
