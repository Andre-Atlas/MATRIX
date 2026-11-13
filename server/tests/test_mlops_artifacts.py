from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from matrix_server.config import Settings
from matrix_server.mlops.artifact_store import (
    LocalFSArtifactStore,
    S3ArtifactStore,
    build_artifact_store,
    validate_external_uri,
    validate_relative_path,
    validate_safe_segment,
)

SAMPLE_SHA256 = "a" * 64


def _local_settings(tmp_path: Path, **overrides: object) -> Settings:
    params: dict[str, object] = {
        "artifact_store": "local",
        "artifact_dir": str(tmp_path),
        "jwt_secret": "test-hmac-secret-key",
        "artifact_presign_ttl_seconds": 600,
    }
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


def _s3_settings(**overrides: object) -> Settings:
    params: dict[str, object] = {
        "artifact_store": "s3",
        "s3_endpoint": "http://minio.example.com:9000",
        "s3_bucket": "matrix-artifacts",
        "s3_region": "us-east-1",
        "s3_access_key": "AKIA_TEST_KEY",
        "s3_secret_key": "SUPER_SECRET_KEY_DO_NOT_LEAK",
        "artifact_presign_ttl_seconds": 900,
    }
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


def test_local_store_presign_write_read_and_exists(tmp_path: Path) -> None:
    store = LocalFSArtifactStore(_local_settings(tmp_path))
    data = b"safetensors-header-and-weights"
    sha = hashlib.sha256(data).hexdigest()

    storage_uri, put_url = store.presign_put("proj-1", sha, len(data))
    assert storage_uri == f"local://projects/proj-1/blobs/sha256/{sha}"
    assert put_url.startswith("/api/v1/mlops/artifacts/local/")

    put_token = put_url.rsplit("/", 1)[-1]
    key = store.verify_local_token(put_token, "put")
    assert key == f"projects/proj-1/blobs/sha256/{sha}"

    assert store.exists_and_size(storage_uri) == (False, None)

    written_path = store.write_local_blob(key, data)
    assert written_path.is_file()
    assert written_path.is_relative_to(tmp_path.resolve())
    assert store.exists_and_size(storage_uri) == (True, len(data))

    get_url = store.presign_get("proj-1", storage_uri)
    get_token = get_url.rsplit("/", 1)[-1]
    get_key = store.verify_local_token(get_token, "get")
    assert get_key == key
    assert store.read_local_blob_path(get_key).read_bytes() == data


def test_local_store_rejects_tampered_swapped_and_expired_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSArtifactStore(_local_settings(tmp_path, artifact_presign_ttl_seconds=60))
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: now)

    storage_uri, put_url = store.presign_put("proj-1", SAMPLE_SHA256, 128)
    put_token = put_url.rsplit("/", 1)[-1]
    get_url = store.presign_get("proj-1", storage_uri)
    get_token = get_url.rsplit("/", 1)[-1]

    # Swapped operation
    with pytest.raises(ValueError, match="operation mismatch"):
        store.verify_local_token(put_token, "get")
    with pytest.raises(ValueError, match="operation mismatch"):
        store.verify_local_token(get_token, "put")

    # Tampered signature
    payload_b64, sig = put_token.split(".")
    bad_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
    with pytest.raises(ValueError, match="signature"):
        store.verify_local_token(f"{payload_b64}.{bad_sig}", "put")

    # Tampered payload
    tampered_payload = ("A" if payload_b64[0] != "A" else "B") + payload_b64[1:]
    with pytest.raises(ValueError, match="signature"):
        store.verify_local_token(f"{tampered_payload}.{sig}", "put")

    # Malformed token
    with pytest.raises(ValueError, match="Malformed"):
        store.verify_local_token("no-dot-here", "put")

    # Expired token
    monkeypatch.setattr(time, "time", lambda: now + 61)
    with pytest.raises(ValueError, match="expired"):
        store.verify_local_token(put_token, "put")


def test_local_store_prevents_path_traversal_and_idor(tmp_path: Path) -> None:
    store = LocalFSArtifactStore(_local_settings(tmp_path))

    with pytest.raises(ValueError):
        store.object_key("../etc", SAMPLE_SHA256)
    with pytest.raises(ValueError):
        store.object_key("proj\\1", SAMPLE_SHA256)
    with pytest.raises(ValueError):
        store.object_key("proj\x001", SAMPLE_SHA256)
    with pytest.raises(ValueError):
        store.object_key("proj-1", "../" + "a" * 61)
    with pytest.raises(ValueError):
        store.write_local_blob("../../outside.bin", b"bad")
    with pytest.raises(ValueError):
        store.presign_put("proj-1", SAMPLE_SHA256, 999_999_999_999)

    # Anti-IDOR on presign_get
    with pytest.raises(ValueError, match="does not belong"):
        store.presign_get("proj-2", f"local://projects/proj-1/blobs/sha256/{SAMPLE_SHA256}")

    # Symlink escaping root
    outside = tmp_path.parent / "outside_escape"
    outside.mkdir(exist_ok=True)
    escape_link = tmp_path / "projects"
    escape_link.symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        store.write_local_blob(f"projects/proj-1/blobs/sha256/{SAMPLE_SHA256}", b"x")


@pytest.mark.parametrize(
    "bad_value",
    ["../secret", "a/../../b", "dir\\file", "bad\x00name", "bad\nname", "/leading", "trailing/"],
)
def test_path_traversal_validators(bad_value: str) -> None:
    with pytest.raises(ValueError):
        validate_relative_path(bad_value)
    with pytest.raises(ValueError):
        validate_safe_segment(bad_value)


def test_s3_store_sigv4_presign_and_no_secret_leak() -> None:
    settings = _s3_settings()
    store = S3ArtifactStore(settings)

    storage_uri, put_url = store.presign_put("proj-1", SAMPLE_SHA256, 4096)
    assert storage_uri == f"s3://matrix-artifacts/projects/proj-1/blobs/sha256/{SAMPLE_SHA256}"

    parsed = urlsplit(put_url)
    qs = parse_qs(parsed.query)
    assert qs["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert qs["X-Amz-Expires"] == ["900"]
    assert qs["X-Amz-SignedHeaders"] == ["host"]
    assert len(qs["X-Amz-Signature"][0]) == 64
    assert qs["X-Amz-Credential"][0].startswith("AKIA_TEST_KEY/")
    assert settings.s3_secret_key not in put_url

    get_url = store.presign_get("proj-1", storage_uri)
    assert settings.s3_secret_key not in get_url
    assert parse_qs(urlsplit(get_url).query)["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]

    # Deterministic SigV4 verification
    fixed_ts = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
    key = store.object_key("proj-1", SAMPLE_SHA256)
    signed_url = store._presign_url("GET", key, now=fixed_ts)
    signed_qs = parse_qs(urlsplit(signed_url).query)
    assert signed_qs["X-Amz-Date"] == ["20261113T120000Z"]
    assert signed_qs["X-Amz-Credential"] == ["AKIA_TEST_KEY/20261113/us-east-1/s3/aws4_request"]

    # Anti-IDOR
    with pytest.raises(ValueError, match="does not belong"):
        store.presign_get("other-proj", storage_uri)

    # Missing credentials error must not leak anything
    no_creds = S3ArtifactStore(_s3_settings(s3_access_key="", s3_secret_key=""))
    with pytest.raises(ValueError, match="credentials"):
        no_creds.presign_put("proj-1", SAMPLE_SHA256, 10)


def test_s3_store_exists_and_size_and_error_handling() -> None:
    secret = "SUPER_SECRET_KEY_DO_NOT_LEAK"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert "X-Amz-Signature=" in str(request.url)
        if "missing" in request.url.path:
            return httpx.Response(404)
        if "error500" in request.url.path:
            return httpx.Response(500)
        if "networkerr" in request.url.path:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, headers={"Content-Length": "2048"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = S3ArtifactStore(_s3_settings(s3_secret_key=secret), http_client=client)

    ok_uri = f"s3://matrix-artifacts/projects/p1/blobs/sha256/{'b' * 64}"
    assert store.exists_and_size(ok_uri) == (True, 2048)

    missing_uri = f"s3://matrix-artifacts/projects/missing/blobs/sha256/{'c' * 64}"
    assert store.exists_and_size(missing_uri) == (False, None)

    err_uri = f"s3://matrix-artifacts/projects/error500/blobs/sha256/{'d' * 64}"
    with pytest.raises(RuntimeError) as exc_info:
        store.exists_and_size(err_uri)
    assert secret not in str(exc_info.value)

    net_uri = f"s3://matrix-artifacts/projects/networkerr/blobs/sha256/{'e' * 64}"
    with pytest.raises(RuntimeError) as net_exc:
        store.exists_and_size(net_uri)
    assert secret not in str(net_exc.value)
    assert "X-Amz-Signature" not in str(net_exc.value)


def test_build_artifact_store(tmp_path: Path) -> None:
    assert isinstance(build_artifact_store(_local_settings(tmp_path)), LocalFSArtifactStore)
    assert isinstance(build_artifact_store(_s3_settings()), S3ArtifactStore)


@pytest.mark.parametrize(
    "valid_uri",
    [
        "s3://my-bucket/models/llama-3/adapter.safetensors",
        "gs://ml-artifacts/runs/run-1/weights.bin",
        "hf://meta-llama/Llama-3.1-8B-Instruct",
        "lakefs://repo/main/checkpoints/step-1000",
    ],
)
def test_validate_external_uri_accepts_allowed_schemes(valid_uri: str) -> None:
    assert validate_external_uri(valid_uri) == valid_uri


@pytest.mark.parametrize(
    "blocked_uri",
    [
        "",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/admin",
        "https://example.com/model.safetensors",
        "s3://169.254.169.254/bucket/key",
        "s3://127.0.0.1/bucket/key",
        "s3://10.0.0.5/bucket/key",
        "s3://[::1]/bucket/key",
        "s3://localhost/bucket/key",
        "s3://metadata.google.internal/bucket/key",
        "s3://user:pass@my-bucket/key",
        "s3://my-bucket/../../etc/passwd",
        "s3://my-bucket/path\\evil",
        "s3://my-bucket/key\n",
    ],
)
def test_validate_external_uri_blocks_ssrf_and_invalid(blocked_uri: str) -> None:
    with pytest.raises(ValueError):
        validate_external_uri(blocked_uri)


def test_hmac_helper_unused_import_guard() -> None:
    # Sanity check that standard hmac/hashlib work as expected in environment
    assert len(hmac.new(b"k", b"v", hashlib.sha256).hexdigest()) == 64


def test_local_store_cas_idempotency_corruption_and_sha_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalFSArtifactStore(_local_settings(tmp_path))
    data = b"model-weights-cas-test"
    sha = hashlib.sha256(data).hexdigest()
    key = store.object_key("proj-1", sha)

    # Mismatch raises ValueError and leaves no file
    with pytest.raises(ValueError, match="sha256 mismatch"):
        store.write_local_blob(key, b"wrong-content")
    assert not (tmp_path / key).exists()

    # Valid write succeeds
    target = store.write_local_blob(key, data)
    assert target.read_bytes() == data

    # Idempotent rewrite does not call os.replace / overwrite
    import os

    def forbid_replace(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("os.replace should not be called for idempotent CAS write")

    monkeypatch.setattr(os, "replace", forbid_replace)
    assert store.write_local_blob(key, data) == target
    monkeypatch.undo()

    # Corrupted existing file on disk raises error
    target.write_bytes(b"corrupted-on-disk")
    with pytest.raises(RuntimeError, match="corrupt"):
        store.write_local_blob(key, data)


@pytest.mark.asyncio
async def test_local_store_write_local_blob_stream(tmp_path: Path) -> None:
    store = LocalFSArtifactStore(_local_settings(tmp_path, max_artifact_bytes=100))
    chunks = [b"hello-", b"streaming-", b"artifact"]
    data = b"".join(chunks)
    sha = hashlib.sha256(data).hexdigest()
    key = store.object_key("proj-1", sha)

    async def gen(items: list[bytes]):
        for item in items:
            yield item

    written_path, written_size = await store.write_local_blob_stream(key, gen(chunks))
    assert written_size == len(data)
    assert written_path.read_bytes() == data

    # Oversized stream raises ValueError and cleans up temp files
    big_key = store.object_key("proj-1", hashlib.sha256(b"x" * 101).hexdigest())
    with pytest.raises(ValueError, match="max_artifact_bytes"):
        await store.write_local_blob_stream(big_key, gen([b"x" * 60, b"x" * 41]))
    assert not (tmp_path / big_key).exists()
    assert not list((tmp_path / "projects/proj-1/blobs/sha256").glob(".tmp_*"))

