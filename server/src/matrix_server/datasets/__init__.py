"""Datasets module for MATRIX: storage, versioning, and multimodal evaluation."""

from matrix_server.datasets.versioning import (
    BranchExistsError,
    BranchNotFoundError,
    CommitNotFoundError,
    DatasetNotFoundError,
    InvalidBranchNameError,
    MergeConflictError,
    VersioningError,
    commit_changes,
    compute_item_content_hash,
    compute_tree_hash,
    create_branch,
    diff_commits,
    find_lca,
    merge_branches,
    revert_branch,
)

__all__ = [
    "BranchExistsError",
    "BranchNotFoundError",
    "CommitNotFoundError",
    "DatasetNotFoundError",
    "InvalidBranchNameError",
    "MergeConflictError",
    "VersioningError",
    "commit_changes",
    "compute_item_content_hash",
    "compute_tree_hash",
    "create_branch",
    "diff_commits",
    "find_lca",
    "merge_branches",
    "revert_branch",
]
