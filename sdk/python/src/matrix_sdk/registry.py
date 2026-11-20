"""Canonical Model & LoRA Registry and bounded Lineage client (T6 / contract G3)."""

from __future__ import annotations

import hashlib
import math
import mimetypes
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from ._net import client_kwargs
from .client import MatrixError, _seg
from .tracking import _resolve_client, current_run

if TYPE_CHECKING:
    from .client import MatrixClient

_CHUNK_BYTES = 8 * 1024 * 1024  # 8 MB
_STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_finite(obj: Any, *, label: str = "value") -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"{label} must be finite (no NaN or Infinity)")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _validate_finite(k, label=label)
            _validate_finite(v, label=label)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _validate_finite(item, label=label)


def _iter_file_chunks(path: Path, chunk_size: int = _CHUNK_BYTES) -> Iterator[bytes]:
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _compute_sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    for chunk in _iter_file_chunks(path):
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def _is_internal_relative_url(url: str) -> bool:
    return url.startswith("/") and not url.startswith("//")


def _validate_external_url(url: str) -> None:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported external URL scheme: {scheme!r}")


class WebhooksClient:
    """CI/CD webhook management for registered models (`/api/v1/models/{id}/webhooks*`)."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    def create(
        self,
        model_id: str,
        url: str,
        events: list[str],
        *,
        secret: str | None = None,
    ) -> dict[str, Any]:
        """Create a signed CI/CD webhook (ADMIN). The plaintext secret is returned once."""
        body: dict[str, Any] = {"url": url, "events": list(events)}
        if secret is not None:
            body["secret"] = secret
        return dict(
            self._client._request(
                "POST",
                f"/api/v1/models/{_seg(model_id)}/webhooks",
                json=body,
            )
        )

    def list(self, model_id: str) -> dict[str, Any]:
        """List webhooks registered for a model (ADMIN; secrets are never returned)."""
        return dict(self._client._request("GET", f"/api/v1/models/{_seg(model_id)}/webhooks"))

    def delete(self, model_id: str, webhook_id: str) -> dict[str, Any]:
        """Delete a model webhook (ADMIN)."""
        return dict(
            self._client._request(
                "DELETE",
                f"/api/v1/models/{_seg(model_id)}/webhooks/{_seg(webhook_id)}",
            )
        )

    def deliveries(
        self,
        model_id: str,
        webhook_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List delivery attempts for a webhook (VIEWER)."""
        return dict(
            self._client._request(
                "GET",
                f"/api/v1/models/{_seg(model_id)}/webhooks/{_seg(webhook_id)}/deliveries",
                params={"limit": int(limit)},
            )
        )


class RegistryClient:
    """Canonical client for Model/LoRA Registry, promotion, webhooks, and bounded lineage."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client
        self.webhooks = WebhooksClient(client)

    # -- models & versions

    def register(
        self,
        name: str,
        *,
        model_type: str = "llm",
        base_model: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a new model in the project registry."""
        body: dict[str, Any] = {
            "name": name,
            "model_type": model_type,
            "tags": list(tags or []),
        }
        if base_model is not None:
            body["base_model"] = base_model
        if description is not None:
            body["description"] = description
        return dict(self._client._request("POST", "/api/v1/mlops/models", json=body))

    def list(self) -> dict[str, Any]:
        """List registered models in the current project."""
        return dict(self._client._request("GET", "/api/v1/mlops/models"))

    def get(self, model_id: str) -> dict[str, Any]:
        """Retrieve a registered model with its versions and aliases."""
        return dict(self._client._request("GET", f"/api/v1/mlops/models/{_seg(model_id)}"))

    def delete(self, model_id: str, *, force: bool = False) -> dict[str, Any]:
        """Delete a registered model (ADMIN; requires force=True if production/champion)."""
        params: dict[str, Any] = {}
        if force:
            params["force"] = "true"
        return dict(self._client._request("DELETE", f"/api/v1/mlops/models/{_seg(model_id)}", params=params))

    def create_version(
        self,
        model_id: str,
        *,
        artifact_id: str | None = None,
        run_id: str | None = None,
        lora_config: dict[str, Any] | None = None,
        signature: dict[str, Any] | None = None,
        metrics_snapshot: dict[str, Any] | None = None,
        stage: str = "none",
        eval_experiment_id: str | None = None,
        safety_scan_id: str | None = None,
        model_card: str | None = None,
    ) -> dict[str, Any]:
        """Create a new immutable version of a registered model."""
        if metrics_snapshot is not None:
            _validate_finite(metrics_snapshot, label="metrics_snapshot")
        body: dict[str, Any] = {
            "stage": stage,
            "metrics_snapshot": dict(metrics_snapshot or {}),
        }
        if artifact_id is not None:
            body["artifact_id"] = artifact_id
        if run_id is not None:
            body["run_id"] = run_id
        if lora_config is not None:
            body["lora_config"] = lora_config
        if signature is not None:
            body["signature"] = signature
        if eval_experiment_id is not None:
            body["eval_experiment_id"] = eval_experiment_id
        if safety_scan_id is not None:
            body["safety_scan_id"] = safety_scan_id
        if model_card is not None:
            body["model_card"] = model_card
        return dict(
            self._client._request(
                "POST",
                f"/api/v1/mlops/models/{_seg(model_id)}/versions",
                json=body,
            )
        )

    def log_model(
        self,
        path: str | os.PathLike[str],
        name: str,
        *,
        model_type: str = "llm",
        base_model: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        run_id: str | None = None,
        stage: str = "none",
        lora_config: dict[str, Any] | None = None,
        signature: dict[str, Any] | None = None,
        metrics_snapshot: dict[str, Any] | None = None,
        model_card: str | None = None,
    ) -> dict[str, Any]:
        """Upload a model file/directory to the canonical artifact store and create a model version.

        If a registered model with `name` already exists, a new version is appended to it.
        When called inside an active `start_run()` context (or with explicit `run_id`), the
        uploaded artifact and model version are linked to that run for lineage tracking.
        """
        src = Path(path)
        if src.is_symlink():
            raise ValueError(f"model path must not be a symlink: {path}")
        if not src.exists():
            raise FileNotFoundError(f"model path does not exist: {path}")

        active = current_run()
        resolved_run_id = run_id or (active.id if active is not None else None)

        tmpdir: str | None = None
        try:
            upload_src = src
            if src.is_dir():
                tmpdir = tempfile.mkdtemp(prefix="matrix-model-")
                upload_src = Path(shutil.make_archive(os.path.join(tmpdir, src.name or name), "zip", src))
            elif not src.is_file():
                raise ValueError(f"model path must be a regular file or directory: {path}")

            sha256_hex, size_bytes = _compute_sha256_and_size(upload_src)
            filename = upload_src.name
            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            headers: dict[str, str] = {
                "X-Filename": filename,
                "X-Sha256": sha256_hex,
                "Content-Type": media_type,
                "Content-Length": str(size_bytes),
            }
            if resolved_run_id is not None:
                headers["X-Owner-Kind"] = "run"
                headers["X-Owner-Id"] = resolved_run_id

            resp = self._client._http.post(
                "/api/v1/artifacts",
                content=_iter_file_chunks(upload_src),
                headers=headers,
                timeout=_STREAM_TIMEOUT,
            )
            resp.raise_for_status()
            artifact = dict(resp.json())
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)

        if resolved_run_id is not None:
            self._client._request(
                "POST",
                f"/api/v1/runs/{_seg(resolved_run_id)}/artifacts",
                json={"artifact_id": artifact["id"], "name": name, "kind": "model"},
            )

        try:
            model = self.register(
                name,
                model_type=model_type,
                base_model=base_model,
                description=description,
                tags=tags,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            items = (self.list().get("items") or [])
            found = next((m for m in items if m.get("name") == name), None)
            if found is None:
                raise MatrixError(f"Model {name!r} already exists but could not be retrieved") from None
            model = dict(found)

        mv = self.create_version(
            str(model["id"]),
            artifact_id=str(artifact["id"]),
            run_id=resolved_run_id,
            lora_config=lora_config,
            signature=signature,
            metrics_snapshot=metrics_snapshot,
            stage=stage,
            model_card=model_card,
        )
        return {
            "id": mv["id"],
            "model_id": model["id"],
            "version": mv["version"],
            "stage": mv.get("stage", stage),
            "model": model,
            "model_version": mv,
            "artifact": artifact,
        }

    # -- promotion, stages, aliases & history (canonical /api/v1/models* routes)

    def promote(
        self,
        model_id: str,
        version: int,
        stage: str,
        *,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Transition a model version to a stage (`none`, `staging`, `production`, `archived`)."""
        body: dict[str, Any] = {"stage": stage}
        if comment is not None:
            body["comment"] = comment
        return dict(
            self._client._request(
                "POST",
                f"/api/v1/models/{_seg(model_id)}/versions/{int(version)}/stage",
                json=body,
            )
        )

    def set_alias(self, model_id: str, alias: str, version: int) -> dict[str, Any]:
        """Point an alias (e.g. `champion`, `production`, `canary`) to a model version."""
        clean_alias = alias.strip().lstrip("@")
        if not clean_alias:
            raise ValueError("alias must not be blank")
        return dict(
            self._client._request(
                "PUT",
                f"/api/v1/models/{_seg(model_id)}/aliases/{_seg(clean_alias)}",
                json={"version": int(version)},
            )
        )

    def delete_alias(self, model_id: str, alias: str) -> dict[str, Any]:
        """Remove an alias from a registered model."""
        clean_alias = alias.strip().lstrip("@")
        if not clean_alias:
            raise ValueError("alias must not be blank")
        return dict(
            self._client._request(
                "DELETE",
                f"/api/v1/models/{_seg(model_id)}/aliases/{_seg(clean_alias)}",
            )
        )

    def history(
        self,
        model_id: str,
        *,
        version: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Retrieve stage transition audit history for a model."""
        params: dict[str, Any] = {"limit": int(limit)}
        if version is not None:
            params["version"] = int(version)
        return dict(
            self._client._request(
                "GET",
                f"/api/v1/models/{_seg(model_id)}/history",
                params=params,
            )
        )

    def files(self, model_id: str, version: int) -> dict[str, Any]:
        """List downloadable artifact files for a model version."""
        return dict(
            self._client._request(
                "GET",
                f"/api/v1/models/{_seg(model_id)}/versions/{int(version)}/files",
            )
        )

    def download(
        self,
        model_id: str,
        *,
        alias: str = "production",
        version: int | None = None,
        dest_dir: str | Path = ".",
    ) -> Path:
        """Download and SHA-256-verify a model version's primary artifact via canonical routes."""
        model_data = self.get(model_id)
        if version is None:
            alias_key = alias.lstrip("@")
            aliases = model_data.get("aliases") or {}
            if alias_key not in aliases:
                raise MatrixError(f"Alias '{alias}' not found for model '{model_id}'")
            version = int(aliases[alias_key])

        files_resp = self.files(model_id, version)
        file_entries = files_resp.get("files") or []
        entry = next((f for f in file_entries if f.get("url")), None)
        if entry is None:
            raise MatrixError(f"Model '{model_id}' version {version} has no downloadable artifact files")

        download_url = str(entry["url"])
        expected_sha256 = str(entry.get("sha256") or "").lower()
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise MatrixError(
                f"Invalid expected SHA-256 digest for model {model_id} v{version}: {expected_sha256!r}"
            )

        if not _is_internal_relative_url(download_url):
            _validate_external_url(download_url)

        dest_root = Path(dest_dir)
        dest_root.mkdir(parents=True, exist_ok=True)
        safe_model_name = Path(str(model_data.get("name") or model_id)).name.replace(" ", "_")
        final_path = dest_root / f"{safe_model_name}-v{version}.bin"

        with tempfile.NamedTemporaryFile(
            dir=dest_root,
            delete=False,
            prefix=f".{safe_model_name}-v{version}-",
            suffix=".part",
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)

        hasher = hashlib.sha256()

        def _stream_into_tmp(http_client: httpx.Client) -> None:
            with http_client.stream("GET", download_url, timeout=_STREAM_TIMEOUT) as resp:
                resp.raise_for_status()
                with tmp_path.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=_CHUNK_BYTES):
                        if chunk:
                            hasher.update(chunk)
                            fh.write(chunk)

        try:
            if _is_internal_relative_url(download_url):
                _stream_into_tmp(self._client._http)
            else:
                # Never forward MATRIX Authorization header to external presigned URLs;
                # honour environment proxy/CA settings via client_kwargs() without verify=False.
                with httpx.Client(timeout=_STREAM_TIMEOUT, **client_kwargs()) as ext_http:
                    _stream_into_tmp(ext_http)

            actual_sha256 = hasher.hexdigest().lower()
            if actual_sha256 != expected_sha256:
                raise MatrixError(
                    f"SHA-256 mismatch for model {model_id} v{version}: "
                    f"expected {expected_sha256}, got {actual_sha256}"
                )
            os.replace(tmp_path, final_path)
            return final_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    # -- webhook shortcuts

    def create_webhook(
        self,
        model_id: str,
        url: str,
        events: list[str],
        *,
        secret: str | None = None,
    ) -> dict[str, Any]:
        return self.webhooks.create(model_id, url, events, secret=secret)

    def list_webhooks(self, model_id: str) -> dict[str, Any]:
        return self.webhooks.list(model_id)

    def delete_webhook(self, model_id: str, webhook_id: str) -> dict[str, Any]:
        return self.webhooks.delete(model_id, webhook_id)

    def webhook_deliveries(self, model_id: str, webhook_id: str, *, limit: int = 100) -> dict[str, Any]:
        return self.webhooks.deliveries(model_id, webhook_id, limit=limit)

    # -- bounded lineage (canonical /api/v1/lineage)

    def lineage(
        self,
        node: str,
        *,
        direction: str = "both",
        depth: int = 3,
        max_nodes: int | None = None,
    ) -> dict[str, Any]:
        """Query the bounded lineage graph (`GET /api/v1/lineage`).

        `node` must be `<kind>:<id>`, e.g. `run:<id>`, `model_version:<id>`, `dataset:<id>`.
        """
        params: dict[str, Any] = {
            "node": node,
            "direction": direction,
            "depth": int(depth),
        }
        if max_nodes is not None:
            params["max_nodes"] = int(max_nodes)
        return dict(self._client._request("GET", "/api/v1/lineage", params=params))


# -- module-level helpers -------------------------------------------------------------------------


def log_model(
    path: str | os.PathLike[str],
    name: str,
    *,
    client: MatrixClient | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Upload a model artifact and register a new model version using the default or given client."""
    http_client, owns = _resolve_client(client)
    try:
        return http_client.registry.log_model(path, name, **kwargs)
    finally:
        if owns:
            http_client._http.close()


def register(
    name: str,
    *,
    client: MatrixClient | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Register a model in the canonical registry using the default or given client."""
    http_client, owns = _resolve_client(client)
    try:
        return http_client.registry.register(name, **kwargs)
    finally:
        if owns:
            http_client._http.close()


def promote(
    model_id: str,
    version: int,
    stage: str,
    *,
    comment: str | None = None,
    client: MatrixClient | None = None,
) -> dict[str, Any]:
    """Promote/transition a model version stage (`POST /api/v1/models/{id}/versions/{v}/stage`)."""
    http_client, owns = _resolve_client(client)
    try:
        return http_client.registry.promote(model_id, version, stage, comment=comment)
    finally:
        if owns:
            http_client._http.close()


def set_alias(
    model_id: str,
    alias: str,
    version: int,
    *,
    client: MatrixClient | None = None,
) -> dict[str, Any]:
    """Set a model alias (`PUT /api/v1/models/{id}/aliases/{alias}`)."""
    http_client, owns = _resolve_client(client)
    try:
        return http_client.registry.set_alias(model_id, alias, version)
    finally:
        if owns:
            http_client._http.close()


def lineage(
    node: str,
    *,
    direction: str = "both",
    depth: int = 3,
    max_nodes: int | None = None,
    client: MatrixClient | None = None,
) -> dict[str, Any]:
    """Query bounded lineage (`GET /api/v1/lineage`)."""
    http_client, owns = _resolve_client(client)
    try:
        return http_client.registry.lineage(node, direction=direction, depth=depth, max_nodes=max_nodes)
    finally:
        if owns:
            http_client._http.close()
