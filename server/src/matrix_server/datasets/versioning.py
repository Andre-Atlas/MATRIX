"""Zero-copy dataset versioning engine (ADR-007 Git-like / LakeFS branching for datasets)."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from matrix_server.storage.models import (
    Dataset,
    DatasetBranch,
    DatasetCommit,
    DatasetCommitItem,
    DatasetItem,
)


class VersioningError(Exception):
    """Base error for dataset versioning operations."""


class DatasetNotFoundError(VersioningError):
    """Raised when a requested dataset is not found."""


class BranchNotFoundError(VersioningError):
    """Raised when a branch does not exist."""


class BranchExistsError(VersioningError):
    """Raised when attempting to create a branch that already exists."""


class CommitNotFoundError(VersioningError):
    """Raised when a commit is not found."""


class InvalidBranchNameError(VersioningError, ValueError):
    """Raised when a branch name violates naming safety rules."""


class MergeConflictError(VersioningError):
    """Raised when a 3-way merge encounters unresolvable conflicts."""

    def __init__(self, conflicts: list[dict[str, Any]], message: str | None = None) -> None:
        self.conflicts = conflicts
        msg = message or f"Merge conflict on {len(conflicts)} item(s)"
        super().__init__(msg)


# --- Canonical Hashing & Tree Hashing --------------------------------------


def compute_item_content_hash(item_dict: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 of canonical JSON representation of an item.

    Sorts keys recursively, rejects NaN/Infinity for anti-tamper security.
    """
    canonical_json = json.dumps(
        item_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_tree_hash(items_dict: dict[str, str]) -> str:
    """Compute tree hash as SHA-256 of sorted `item_key:content_hash` pairs."""
    sorted_pairs = [f"{k}:{items_dict[k]}" for k in sorted(items_dict.keys())]
    raw = "\n".join(sorted_pairs).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --- Validation -----------------------------------------------------------

_SAFE_BRANCH_REGEX = re.compile(r"^[a-zA-Z0-9_\-\./]+$")


def validate_branch_name(name: str) -> None:
    """Validate that a branch name is safe against path traversal and SQL injection."""
    if not name or len(name) > 200:
        raise InvalidBranchNameError(
            f"Branch name must be between 1 and 200 characters, got {len(name) if name else 0}"
        )
    if ".." in name:
        raise InvalidBranchNameError("Branch name must not contain path traversal '..'")
    if name.startswith("/") or name.endswith("/"):
        raise InvalidBranchNameError("Branch name must not start or end with '/'")
    if "//" in name:
        raise InvalidBranchNameError("Branch name must not contain consecutive slashes '//'")
    if name.startswith(".") or name.endswith(".lock"):
        raise InvalidBranchNameError("Branch name must not start with '.' or end with '.lock'")
    if not _SAFE_BRANCH_REGEX.match(name):
        raise InvalidBranchNameError(f"Branch name contains invalid characters: '{name}'")


# --- Branch Operations ----------------------------------------------------


def create_branch(
    session: Session,
    project_id: str,
    dataset_id: str,
    name: str,
    from_branch: str | None = None,
    from_commit_id: str | None = None,
    created_by: str | None = None,
) -> DatasetBranch:
    """Create a zero-copy branch pointer for a dataset."""
    validate_branch_name(name)

    ds = session.scalars(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    ).first()
    if ds is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' not found in project '{project_id}'")

    existing = session.scalars(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.project_id == project_id,
            DatasetBranch.name == name,
        )
    ).first()
    if existing is not None:
        raise BranchExistsError(f"Branch '{name}' already exists for dataset '{dataset_id}'")

    head_commit_id: str | None = None
    if from_commit_id is not None:
        commit = session.scalars(
            select(DatasetCommit).where(
                DatasetCommit.id == from_commit_id,
                DatasetCommit.dataset_id == dataset_id,
                DatasetCommit.project_id == project_id,
            )
        ).first()
        if commit is None:
            raise CommitNotFoundError(f"Commit '{from_commit_id}' not found in dataset '{dataset_id}'")
        head_commit_id = commit.id
    elif from_branch is not None:
        src = session.scalars(
            select(DatasetBranch).where(
                DatasetBranch.dataset_id == dataset_id,
                DatasetBranch.project_id == project_id,
                DatasetBranch.name == from_branch,
            )
        ).first()
        if src is None:
            raise BranchNotFoundError(f"Source branch '{from_branch}' not found in dataset '{dataset_id}'")
        head_commit_id = src.head_commit_id
    else:
        default_branch = session.scalars(
            select(DatasetBranch).where(
                DatasetBranch.dataset_id == dataset_id,
                DatasetBranch.project_id == project_id,
                DatasetBranch.is_default.is_(True),
            )
        ).first()
        if default_branch is not None:
            head_commit_id = default_branch.head_commit_id

    existing_branches_count = (
        session.scalar(
            select(func.count(DatasetBranch.id)).where(
                DatasetBranch.dataset_id == dataset_id,
                DatasetBranch.project_id == project_id,
            )
        )
        or 0
    )
    is_default = (existing_branches_count == 0) or (name in ("main", "master") and existing_branches_count == 0)

    branch = DatasetBranch(
        project_id=project_id,
        dataset_id=dataset_id,
        name=name,
        head_commit_id=head_commit_id,
        is_default=is_default,
        created_by=created_by,
    )
    session.add(branch)
    session.flush()
    return branch


# --- Commit Operations ----------------------------------------------------


def _load_commit_items_map(session: Session, commit_id: str | None) -> dict[str, dict[str, Any]]:
    """Load items for a commit as a dictionary indexed by `item_key`."""
    if not commit_id:
        return {}
    items = session.scalars(
        select(DatasetCommitItem).where(DatasetCommitItem.commit_id == commit_id)
    ).all()
    return {
        it.item_key: {
            "item_key": it.item_key,
            "content_hash": it.content_hash,
            "input": it.input,
            "expected_output": it.expected_output,
            "context": it.context or [],
            "media": it.media or [],
            "metadata": it.metadata_ or {},
            "artifact_refs": it.artifact_refs or [],
        }
        for it in items
    }


def commit_changes(
    session: Session,
    project_id: str,
    dataset_id: str,
    branch_name: str,
    message: str,
    changes: dict[str, Any] | list[dict[str, Any]] | None = None,
    snapshot_from_working_tree: bool = False,
    author: str | None = None,
) -> DatasetCommit:
    """Create an immutable commit on a branch.

    If `snapshot_from_working_tree`: reads current `dataset_items` for the dataset as snapshot.
    If `changes`: applies `upsert` and `delete` deltas against parent commit items.
    Computes `item_count`, `stats`, `tree_hash`, stores commit + commit items,
    and advances the branch `head_commit_id`.
    """
    ds = session.scalars(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    ).first()
    if ds is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' not found in project '{project_id}'")

    branch = session.scalars(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.project_id == project_id,
            DatasetBranch.name == branch_name,
        )
    ).first()

    if branch is None:
        # If no branches exist and committing to main, auto-initialize main branch
        total_branches = session.scalar(
            select(func.count(DatasetBranch.id)).where(
                DatasetBranch.dataset_id == dataset_id,
                DatasetBranch.project_id == project_id,
            )
        )
        if total_branches == 0 and branch_name in ("main", "master"):
            branch = create_branch(session, project_id, dataset_id, branch_name, created_by=author)
        else:
            raise BranchNotFoundError(f"Branch '{branch_name}' not found for dataset '{dataset_id}'")

    parent_commit_id = branch.head_commit_id
    parent_items = _load_commit_items_map(session, parent_commit_id)

    new_items: dict[str, dict[str, Any]] = {}

    if snapshot_from_working_tree:
        working_items = session.scalars(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == dataset_id, DatasetItem.project_id == project_id)
            .order_by(DatasetItem.created_at, DatasetItem.id)
        ).all()

        for it in working_items:
            key = (it.metadata_ or {}).get("item_key") or it.id
            item_payload = {
                "input": it.input or {},
                "expected_output": it.expected_output,
                "context": it.context or [],
                "media": it.media or [],
                "metadata": it.metadata_ or {},
            }
            content_hash = compute_item_content_hash(item_payload)
            new_items[key] = {
                "item_key": key,
                "content_hash": content_hash,
                "input": item_payload["input"],
                "expected_output": item_payload["expected_output"],
                "context": item_payload["context"],
                "media": item_payload["media"],
                "metadata": item_payload["metadata"],
                "artifact_refs": [],
            }
    elif changes is not None:
        new_items = dict(parent_items)
        if isinstance(changes, dict):
            upserts = changes.get("upsert", changes.get("upserts", []))
            deletes = changes.get("delete", changes.get("deletes", []))
        elif isinstance(changes, list):
            upserts = [c for c in changes if c.get("op", "upsert") != "delete"]
            deletes = [c.get("item_key") for c in changes if c.get("op") == "delete"]
        else:
            upserts, deletes = [], []

        for del_key in deletes:
            if isinstance(del_key, dict):
                del_key = del_key.get("item_key")
            if del_key:
                new_items.pop(str(del_key), None)

        for up in upserts:
            key = str(up.get("item_key") or up.get("id") or secrets.token_hex(8))
            item_payload = {
                "input": up.get("input", {}),
                "expected_output": up.get("expected_output"),
                "context": up.get("context", []),
                "media": up.get("media", []),
                "metadata": up.get("metadata", up.get("metadata_", {})),
            }
            content_hash = up.get("content_hash") or compute_item_content_hash(item_payload)
            new_items[key] = {
                "item_key": key,
                "content_hash": content_hash,
                "input": item_payload["input"],
                "expected_output": item_payload["expected_output"],
                "context": item_payload["context"],
                "media": item_payload["media"],
                "metadata": item_payload["metadata"],
                "artifact_refs": up.get("artifact_refs", []),
            }
    else:
        # Empty commit (preserves parent snapshot)
        new_items = dict(parent_items)

    # Compute delta stats
    added_keys = [k for k in new_items if k not in parent_items]
    removed_keys = [k for k in parent_items if k not in new_items]
    modified_keys = [
        k for k in new_items if k in parent_items and new_items[k]["content_hash"] != parent_items[k]["content_hash"]
    ]
    unchanged_keys = [
        k for k in new_items if k in parent_items and new_items[k]["content_hash"] == parent_items[k]["content_hash"]
    ]

    stats = {
        "added": len(added_keys),
        "removed": len(removed_keys),
        "modified": len(modified_keys),
        "unchanged": len(unchanged_keys),
    }

    tree_hash = compute_tree_hash({k: it["content_hash"] for k, it in new_items.items()})

    commit = DatasetCommit(
        project_id=project_id,
        dataset_id=dataset_id,
        parent_commit_id=parent_commit_id,
        second_parent_commit_id=None,
        tree_hash=tree_hash,
        message=message,
        author=author,
        item_count=len(new_items),
        stats=stats,
    )
    session.add(commit)
    session.flush()

    # Bulk insert commit items
    for it in new_items.values():
        ci = DatasetCommitItem(
            project_id=project_id,
            commit_id=commit.id,
            item_key=it["item_key"],
            content_hash=it["content_hash"],
            input=it["input"],
            expected_output=it["expected_output"],
            context=it["context"],
            media=it["media"],
            metadata_=it["metadata"],
            artifact_refs=it["artifact_refs"],
        )
        session.add(ci)

    branch.head_commit_id = commit.id
    session.flush()
    return commit


# --- Diff Operations ------------------------------------------------------


def diff_commits(
    session: Session,
    project_id: str,
    dataset_id: str,
    base_commit_id: str | None,
    head_commit_id: str,
) -> dict[str, Any]:
    """Compare items between base_commit_id and head_commit_id."""
    ds = session.scalars(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    ).first()
    if ds is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' not found in project '{project_id}'")

    head_commit = session.scalars(
        select(DatasetCommit).where(
            DatasetCommit.id == head_commit_id,
            DatasetCommit.dataset_id == dataset_id,
            DatasetCommit.project_id == project_id,
        )
    ).first()
    if head_commit is None:
        raise CommitNotFoundError(f"Head commit '{head_commit_id}' not found")

    head_items = session.scalars(
        select(DatasetCommitItem).where(DatasetCommitItem.commit_id == head_commit_id)
    ).all()
    head_map = {it.item_key: it.content_hash for it in head_items}

    if base_commit_id:
        base_commit = session.scalars(
            select(DatasetCommit).where(
                DatasetCommit.id == base_commit_id,
                DatasetCommit.dataset_id == dataset_id,
                DatasetCommit.project_id == project_id,
            )
        ).first()
        if base_commit is None:
            raise CommitNotFoundError(f"Base commit '{base_commit_id}' not found")
        base_items = session.scalars(
            select(DatasetCommitItem).where(DatasetCommitItem.commit_id == base_commit_id)
        ).all()
        base_map = {it.item_key: it.content_hash for it in base_items}
    else:
        base_map = {}

    added = sorted(k for k in head_map if k not in base_map)
    removed = sorted(k for k in base_map if k not in head_map)
    modified = sorted(k for k in head_map if k in base_map and base_map[k] != head_map[k])

    return {
        "base_commit_id": base_commit_id,
        "head_commit_id": head_commit_id,
        "added": added,
        "removed": removed,
        "modified": modified,
        "stats": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
        },
    }


# --- Lowest Common Ancestor (LCA) -----------------------------------------


def find_lca(
    session: Session,
    dataset_id: str,
    commit_a_id: str | None,
    commit_b_id: str | None,
) -> str | None:
    """Find the Lowest Common Ancestor (LCA) traversing commit parents."""
    if not commit_a_id or not commit_b_id:
        return None
    if commit_a_id == commit_b_id:
        return commit_a_id

    rows = session.execute(
        select(DatasetCommit.id, DatasetCommit.parent_commit_id, DatasetCommit.second_parent_commit_id).where(
            DatasetCommit.dataset_id == dataset_id
        )
    ).all()

    parents_map: dict[str, list[str]] = {}
    for cid, p1, p2 in rows:
        parents: list[str] = []
        if p1:
            parents.append(p1)
        if p2:
            parents.append(p2)
        parents_map[cid] = parents

    def get_ancestors(root_id: str) -> set[str]:
        seen = set()
        stack = [root_id]
        while stack:
            curr = stack.pop()
            if curr in seen:
                continue
            seen.add(curr)
            for p in parents_map.get(curr, []):
                if p not in seen:
                    stack.append(p)
        return seen

    ancestors_a = get_ancestors(commit_a_id)
    ancestors_b = get_ancestors(commit_b_id)
    common = ancestors_a & ancestors_b
    if not common:
        return None

    # Eliminate any node that is an ancestor of another node in common
    candidates = set(common)
    for node in common:
        for p in parents_map.get(node, []):
            if p in candidates:
                candidates -= get_ancestors(p)

    return next(iter(candidates)) if candidates else None


# --- Merge Operations -----------------------------------------------------


def merge_branches(
    session: Session,
    project_id: str,
    dataset_id: str,
    source_branch: str,
    target_branch: str,
    conflict_strategy: str = "fail",
    message: str | None = None,
    author: str | None = None,
) -> DatasetCommit:
    """Merge source_branch into target_branch with fast-forward or 3-way merge."""
    if conflict_strategy not in ("fail", "ours", "theirs"):
        raise ValueError(f"Invalid conflict_strategy: {conflict_strategy}. Expected fail, ours, or theirs.")

    ds = session.scalars(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    ).first()
    if ds is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' not found in project '{project_id}'")

    src = session.scalars(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.project_id == project_id,
            DatasetBranch.name == source_branch,
        )
    ).first()
    if src is None:
        raise BranchNotFoundError(f"Source branch '{source_branch}' not found")

    tgt = session.scalars(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.project_id == project_id,
            DatasetBranch.name == target_branch,
        )
    ).first()
    if tgt is None:
        raise BranchNotFoundError(f"Target branch '{target_branch}' not found")

    if src.head_commit_id is None:
        # Nothing in source to merge
        if tgt.head_commit_id:
            res = session.get(DatasetCommit, tgt.head_commit_id)
            if res is not None:
                return res
        raise CommitNotFoundError("No commits found on source or target branch")

    if tgt.head_commit_id is None:
        # Fast-forward empty target to source head
        tgt.head_commit_id = src.head_commit_id
        session.flush()
        target_head = session.get(DatasetCommit, src.head_commit_id)
        if target_head is None:
            raise CommitNotFoundError(f"Commit '{src.head_commit_id}' not found")
        return target_head

    if src.head_commit_id == tgt.head_commit_id:
        # Already identical
        same = session.get(DatasetCommit, tgt.head_commit_id)
        if same is None:
            raise CommitNotFoundError(f"Commit '{tgt.head_commit_id}' not found")
        return same

    lca_id = find_lca(session, dataset_id, src.head_commit_id, tgt.head_commit_id)

    # 1. Fast-forward merge: target_head == lca
    if tgt.head_commit_id == lca_id:
        tgt.head_commit_id = src.head_commit_id
        session.flush()
        ff_commit = session.get(DatasetCommit, src.head_commit_id)
        if ff_commit is None:
            raise CommitNotFoundError(f"Commit '{src.head_commit_id}' not found")
        return ff_commit

    # 2. Already up-to-date: source_head == lca
    if src.head_commit_id == lca_id:
        up_to_date = session.get(DatasetCommit, tgt.head_commit_id)
        if up_to_date is None:
            raise CommitNotFoundError(f"Commit '{tgt.head_commit_id}' not found")
        return up_to_date

    # 3. 3-Way merge
    base_items = _load_commit_items_map(session, lca_id)
    ours_items = _load_commit_items_map(session, tgt.head_commit_id)
    theirs_items = _load_commit_items_map(session, src.head_commit_id)

    all_keys = sorted(set(base_items.keys()) | set(ours_items.keys()) | set(theirs_items.keys()))
    merged_items: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []

    for k in all_keys:
        in_base = k in base_items
        in_ours = k in ours_items
        in_theirs = k in theirs_items

        h_base = base_items[k]["content_hash"] if in_base else None
        h_ours = ours_items[k]["content_hash"] if in_ours else None
        h_theirs = theirs_items[k]["content_hash"] if in_theirs else None

        if h_ours == h_theirs:
            # Both sides agree (both kept/modified identically, or both deleted)
            if in_ours:
                merged_items[k] = ours_items[k]
        elif h_ours == h_base and h_theirs != h_base:
            # Ours didn't touch it; theirs modified or deleted it
            if in_theirs:
                merged_items[k] = theirs_items[k]
        elif h_theirs == h_base and h_ours != h_base:
            # Theirs didn't touch it; ours modified or deleted it
            if in_ours:
                merged_items[k] = ours_items[k]
        else:
            # Conflict: both changed or added with different content
            conflict_entry = {
                "item_key": k,
                "base_hash": h_base,
                "ours_hash": h_ours,
                "theirs_hash": h_theirs,
            }
            conflicts.append(conflict_entry)

            if conflict_strategy == "ours" and in_ours:
                merged_items[k] = ours_items[k]
            elif conflict_strategy == "theirs" and in_theirs:
                merged_items[k] = theirs_items[k]

    if conflicts and conflict_strategy == "fail":
        raise MergeConflictError(conflicts, f"Merge conflict on {len(conflicts)} items between '{source_branch}' and '{target_branch}'")

    tree_hash = compute_tree_hash({k: it["content_hash"] for k, it in merged_items.items()})
    msg = message or f"Merge branch '{source_branch}' into '{target_branch}'"

    # Compute stats relative to target head (ours)
    added = [k for k in merged_items if k not in ours_items]
    removed = [k for k in ours_items if k not in merged_items]
    modified = [
        k for k in merged_items if k in ours_items and merged_items[k]["content_hash"] != ours_items[k]["content_hash"]
    ]

    stats = {
        "added": len(added),
        "removed": len(removed),
        "modified": len(modified),
        "conflicts_resolved": len(conflicts),
    }

    merge_commit = DatasetCommit(
        project_id=project_id,
        dataset_id=dataset_id,
        parent_commit_id=tgt.head_commit_id,
        second_parent_commit_id=src.head_commit_id,
        tree_hash=tree_hash,
        message=msg,
        author=author,
        item_count=len(merged_items),
        stats=stats,
    )
    session.add(merge_commit)
    session.flush()

    for it in merged_items.values():
        ci = DatasetCommitItem(
            project_id=project_id,
            commit_id=merge_commit.id,
            item_key=it["item_key"],
            content_hash=it["content_hash"],
            input=it["input"],
            expected_output=it["expected_output"],
            context=it["context"],
            media=it["media"],
            metadata_=it["metadata"],
            artifact_refs=it["artifact_refs"],
        )
        session.add(ci)

    tgt.head_commit_id = merge_commit.id
    session.flush()
    return merge_commit


# --- Revert Operations ----------------------------------------------------


def revert_branch(
    session: Session,
    project_id: str,
    dataset_id: str,
    branch_name: str,
    target_commit_id: str,
    message: str | None = None,
    author: str | None = None,
) -> DatasetCommit:
    """Create a new commit on `branch_name` restoring the snapshot of `target_commit_id`."""
    ds = session.scalars(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    ).first()
    if ds is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' not found in project '{project_id}'")

    branch = session.scalars(
        select(DatasetBranch).where(
            DatasetBranch.dataset_id == dataset_id,
            DatasetBranch.project_id == project_id,
            DatasetBranch.name == branch_name,
        )
    ).first()
    if branch is None:
        raise BranchNotFoundError(f"Branch '{branch_name}' not found for dataset '{dataset_id}'")

    target_commit = session.scalars(
        select(DatasetCommit).where(
            DatasetCommit.id == target_commit_id,
            DatasetCommit.dataset_id == dataset_id,
            DatasetCommit.project_id == project_id,
        )
    ).first()
    if target_commit is None:
        raise CommitNotFoundError(f"Target commit '{target_commit_id}' not found in dataset '{dataset_id}'")

    target_items = _load_commit_items_map(session, target_commit.id)
    current_items = _load_commit_items_map(session, branch.head_commit_id)

    added = [k for k in target_items if k not in current_items]
    removed = [k for k in current_items if k not in target_items]
    modified = [
        k for k in target_items if k in current_items and target_items[k]["content_hash"] != current_items[k]["content_hash"]
    ]

    stats = {
        "added": len(added),
        "removed": len(removed),
        "modified": len(modified),
        "reverted_to": target_commit_id,
    }

    msg = message or f"Revert branch '{branch_name}' to commit {target_commit_id}"

    revert_commit = DatasetCommit(
        project_id=project_id,
        dataset_id=dataset_id,
        parent_commit_id=branch.head_commit_id,
        second_parent_commit_id=None,
        tree_hash=target_commit.tree_hash,
        message=msg,
        author=author,
        item_count=len(target_items),
        stats=stats,
    )
    session.add(revert_commit)
    session.flush()

    for it in target_items.values():
        ci = DatasetCommitItem(
            project_id=project_id,
            commit_id=revert_commit.id,
            item_key=it["item_key"],
            content_hash=it["content_hash"],
            input=it["input"],
            expected_output=it["expected_output"],
            context=it["context"],
            media=it["media"],
            metadata_=it["metadata"],
            artifact_refs=it["artifact_refs"],
        )
        session.add(ci)

    branch.head_commit_id = revert_commit.id
    session.flush()
    return revert_commit
