"""Datasets SDK client: golden datasets, multimodal tables, and zero-copy versioning engine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .client import _seg

if TYPE_CHECKING:
    from .client import MatrixClient


class DatasetsClient:
    """Client for matrix datasets, items, and zero-copy branching/commit versioning."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    def create(self, name: str, description: str | None = None) -> dict[str, Any]:
        """Create a new dataset."""
        body: dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        return self._client._request("POST", "/api/v1/datasets", json=body)

    def get(self, dataset_id: str, limit: int = 500, cursor: str | None = None) -> dict[str, Any]:
        """Retrieve dataset with one page of working items."""
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._client._request("GET", f"/api/v1/datasets/{_seg(dataset_id)}", params=params)

    def list(self) -> dict[str, Any]:
        """List all datasets in current project."""
        return self._client._request("GET", "/api/v1/datasets")

    def add_item(
        self,
        dataset_id: str,
        input: dict[str, Any] | Any,
        expected_output: str | None = None,
        context: list[str] | None = None,
        media: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a working item (with optional multimodal media) to a dataset."""
        body: dict[str, Any] = {"input": input}
        if expected_output is not None:
            body["expected_output"] = expected_output
        if context is not None:
            body["context"] = context
        if media is not None:
            body["media"] = media
        if metadata is not None:
            body["metadata"] = metadata
        return self._client._request("POST", f"/api/v1/datasets/{_seg(dataset_id)}/items", json=body)

    def list_items(
        self,
        dataset_id: str,
        limit: int = 500,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List working items for a dataset."""
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._client._request("GET", f"/api/v1/datasets/{_seg(dataset_id)}/items", params=params)

    # --- Zero-Copy Branching & Versioning -----------------------------------

    def create_branch(
        self,
        dataset_id: str,
        name: str,
        from_branch: str | None = None,
        from_commit_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new zero-copy branch from an existing branch or commit."""
        body: dict[str, Any] = {"name": name}
        if from_branch is not None:
            body["from_branch"] = from_branch
        if from_commit_id is not None:
            body["from_commit_id"] = from_commit_id
        return self._client._request("POST", f"/api/v1/datasets/{_seg(dataset_id)}/branches", json=body)

    def list_branches(self, dataset_id: str) -> list[dict[str, Any]]:
        """List all branches for a dataset."""
        res = self._client._request("GET", f"/api/v1/datasets/{_seg(dataset_id)}/branches")
        if isinstance(res, dict) and "items" in res:
            return res["items"]
        return res

    def commit(
        self,
        dataset_id: str,
        branch: str,
        message: str,
        changes: list[dict[str, Any]] | None = None,
        snapshot_from_working_tree: bool = False,
    ) -> dict[str, Any]:
        """Commit changes or snap the working tree to a branch."""
        body: dict[str, Any] = {
            "branch": branch,
            "message": message,
            "snapshot_from_working_tree": snapshot_from_working_tree,
        }
        if changes is not None:
            body["changes"] = changes
        return self._client._request("POST", f"/api/v1/datasets/{_seg(dataset_id)}/commits", json=body)

    def list_commits(
        self,
        dataset_id: str,
        branch: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List commits for a dataset, optionally filtered by branch history."""
        params: dict[str, Any] = {"limit": limit}
        if branch is not None:
            params["branch"] = branch
        if cursor is not None:
            params["cursor"] = cursor
        return self._client._request("GET", f"/api/v1/datasets/{_seg(dataset_id)}/commits", params=params)

    def get_commit_items(
        self,
        dataset_id: str,
        commit_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List versioned items contained within a specific commit."""
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return self._client._request(
            "GET",
            f"/api/v1/datasets/{_seg(dataset_id)}/commits/{_seg(commit_id)}/items",
            params=params,
        )

    def diff(
        self,
        dataset_id: str,
        base: str,
        head: str,
    ) -> dict[str, Any]:
        """Diff items between two commits or branch heads."""
        params = {"base": base, "head": head}
        return self._client._request("GET", f"/api/v1/datasets/{_seg(dataset_id)}/diff", params=params)

    def merge(
        self,
        dataset_id: str,
        source_branch: str,
        target_branch: str,
        conflict_strategy: str = "fail",
        message: str | None = None,
    ) -> dict[str, Any]:
        """Merge source_branch into target_branch."""
        body: dict[str, Any] = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "conflict_strategy": conflict_strategy,
        }
        if message is not None:
            body["message"] = message
        return self._client._request("POST", f"/api/v1/datasets/{_seg(dataset_id)}/merge", json=body)

    def revert(
        self,
        dataset_id: str,
        branch: str,
        target_commit_id: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        """Revert a branch to a target commit state."""
        body: dict[str, Any] = {
            "branch": branch,
            "target_commit_id": target_commit_id,
        }
        if message is not None:
            body["message"] = message
        return self._client._request("POST", f"/api/v1/datasets/{_seg(dataset_id)}/revert", json=body)
