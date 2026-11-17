"""Comprehensive tests for Zero-Copy Data Versioning engine & API (ADR-007 / C5)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from matrix_server.api import datasets
from matrix_server.app import create_app
from matrix_server.config import Settings
from matrix_server.datasets.versioning import (
    BranchExistsError,
    BranchNotFoundError,
    CommitNotFoundError,
    InvalidBranchNameError,
    MergeConflictError,
    commit_changes,
    compute_item_content_hash,
    compute_tree_hash,
    create_branch,
    diff_commits,
    find_lca,
    merge_branches,
    revert_branch,
    validate_branch_name,
)
from matrix_server.storage.db import Base, init_db, make_engine, make_session_factory
from matrix_server.storage.models import Dataset, DatasetItem

ADMIN = {"Authorization": "Bearer adm"}
PG_URL = os.environ.get("MATRIX_TEST_DATABASE_URL")


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(params=["sqlite", "postgres"])
def session(request, tmp_path):
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'versioning_engine.db'}")
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        eng = make_engine(PG_URL)
        Base.metadata.drop_all(eng)
    init_db(eng)
    s = make_session_factory(eng)()
    yield s
    s.close()
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(params=["sqlite", "postgres"])
def client_env(request, tmp_path):
    if request.param == "sqlite":
        db_url = f"sqlite:///{tmp_path / 'versioning_api.db'}"
    else:
        if not PG_URL:
            pytest.skip("MATRIX_TEST_DATABASE_URL not set")
        db_url = PG_URL
        eng = make_engine(db_url)
        Base.metadata.drop_all(eng)
        eng.dispose()

    app = create_app(Settings(database_url=db_url, admin_token="adm"))
    if not any(getattr(r, "path", "") == "/api/v1/datasets" for r in app.routes):
        app.include_router(datasets.router)
    client = TestClient(app)

    # Setup tenant one and two
    resp1 = client.post("/api/v1/admin/projects", json={"name": "proj_alpha"}, headers=ADMIN)
    assert resp1.status_code == 201
    auth_alpha = {"Authorization": f"Bearer {resp1.json()['api_key']}"}

    resp2 = client.post("/api/v1/admin/projects", json={"name": "proj_beta"}, headers=ADMIN)
    assert resp2.status_code == 201
    auth_beta = {"Authorization": f"Bearer {resp2.json()['api_key']}"}

    yield client, auth_alpha, auth_beta

    client.app.state.db_engine.dispose()
    if request.param == "postgres":
        eng = make_engine(db_url)
        Base.metadata.drop_all(eng)
        eng.dispose()


# --- Unit Tests: Hashing & Validation ------------------------------------


def test_canonical_hashing_and_anti_nan():
    # Order-independent hashing
    item1 = {"input": {"b": 2, "a": 1}, "expected_output": "out", "context": ["c1"]}
    item2 = {"expected_output": "out", "input": {"a": 1, "b": 2}, "context": ["c1"]}
    assert compute_item_content_hash(item1) == compute_item_content_hash(item2)

    # Different content produces different hash
    item3 = {"input": {"b": 3, "a": 1}, "expected_output": "out", "context": ["c1"]}
    assert compute_item_content_hash(item1) != compute_item_content_hash(item3)

    # Rejection of NaN / Infinity
    with pytest.raises(ValueError):
        compute_item_content_hash({"val": float("nan")})

    with pytest.raises(ValueError):
        compute_item_content_hash({"val": float("inf")})


def test_compute_tree_hash():
    items = {"k2": "hash2", "k1": "hash1", "k3": "hash3"}
    th1 = compute_tree_hash(items)
    # Shuffled order produces identical tree hash
    th2 = compute_tree_hash({"k3": "hash3", "k1": "hash1", "k2": "hash2"})
    assert th1 == th2
    assert len(th1) == 64

    # Empty tree
    assert len(compute_tree_hash({})) == 64


def test_branch_name_validation():
    # Valid names
    for valid in ["main", "feature/new-evals", "experiment_123", "v1.0.0", "team/alice/test"]:
        validate_branch_name(valid)

    # Invalid names
    for invalid in [
        "",
        "../path/traversal",
        "/leading-slash",
        "trailing-slash/",
        "double//slash",
        ".hidden",
        "feature.lock",
        "has spaces",
        "has*wildcards",
        "a" * 201,
    ]:
        with pytest.raises(InvalidBranchNameError):
            validate_branch_name(invalid)


# --- Engine Direct Tests --------------------------------------------------


def test_branch_creation_and_empty_commit(session):
    ds = Dataset(project_id="p1", name="test_ds")
    session.add(ds)
    session.flush()

    # First branch is default
    main_br = create_branch(session, "p1", ds.id, "main")
    assert main_br.name == "main"
    assert main_br.is_default is True
    assert main_br.head_commit_id is None

    # Cannot create duplicate branch
    with pytest.raises(BranchExistsError):
        create_branch(session, "p1", ds.id, "main")

    # Empty commit
    c1 = commit_changes(session, "p1", ds.id, "main", "Initial empty commit", author="alice")
    assert c1.parent_commit_id is None
    assert c1.item_count == 0
    assert c1.stats == {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    assert main_br.head_commit_id == c1.id

    # Secondary branch from main
    feat_br = create_branch(session, "p1", ds.id, "feature/exp", from_branch="main")
    assert feat_br.head_commit_id == c1.id
    assert feat_br.is_default is False

    # Non-existent branch and commit
    with pytest.raises(BranchNotFoundError):
        create_branch(session, "p1", ds.id, "feat2", from_branch="nonexistent")
    with pytest.raises(CommitNotFoundError):
        create_branch(session, "p1", ds.id, "feat3", from_commit_id="nonexistent_id")


def test_commit_from_working_tree_and_deltas(session):
    ds = Dataset(project_id="p1", name="working_tree_ds")
    session.add(ds)
    session.flush()

    # Add 2 items to working tree
    it1 = DatasetItem(
        project_id="p1",
        dataset_id=ds.id,
        input={"prompt": "hello"},
        expected_output="world",
        media=[{"kind": "image", "url": "https://example.com/1.png"}],
    )
    it2 = DatasetItem(
        project_id="p1",
        dataset_id=ds.id,
        input={"prompt": "test"},
        expected_output="pass",
    )
    session.add_all([it1, it2])
    session.flush()

    # Commit from working tree (auto-creates main branch)
    c1 = commit_changes(
        session,
        "p1",
        ds.id,
        "main",
        "Snapshot working tree",
        snapshot_from_working_tree=True,
    )
    assert c1.item_count == 2
    assert c1.stats["added"] == 2
    assert c1.stats["removed"] == 0

    # Commit delta on feature branch
    create_branch(session, "p1", ds.id, "dev", from_branch="main")

    # Update item 1 and add item 3, delete item 2
    changes = {
        "upsert": [
            {
                "item_key": it1.id,
                "input": {"prompt": "hello modified"},
                "expected_output": "world v2",
            },
            {
                "item_key": "custom_key_3",
                "input": {"prompt": "item 3"},
                "expected_output": "three",
            },
        ],
        "delete": [it2.id],
    }
    c2 = commit_changes(session, "p1", ds.id, "dev", "Apply deltas", changes=changes)
    assert c2.item_count == 2
    assert c2.stats["added"] == 1
    assert c2.stats["removed"] == 1
    assert c2.stats["modified"] == 1

    # Diff between c1 and c2
    diff = diff_commits(session, "p1", ds.id, c1.id, c2.id)
    assert diff["added"] == ["custom_key_3"]
    assert diff["removed"] == [it2.id]
    assert diff["modified"] == [it1.id]
    assert diff["stats"] == {"added": 1, "removed": 1, "modified": 1}


def test_lca_and_merges(session):
    ds = Dataset(project_id="p1", name="merge_ds")
    session.add(ds)
    session.flush()

    # Commit 1 on main: base
    base_changes = {
        "upsert": [
            {"item_key": "k_common", "input": {"v": 1}},
            {"item_key": "k_conflict", "input": {"v": "base"}},
            {"item_key": "k_main_only", "input": {"v": "main_base"}},
        ]
    }
    c_base = commit_changes(session, "p1", ds.id, "main", "Base commit", changes=base_changes)

    # Branch feature
    create_branch(session, "p1", ds.id, "feature", from_branch="main")

    # Fast-forward merge test:
    # Feature makes a commit, main is untouched
    feat_changes1 = {"upsert": [{"item_key": "k_feat", "input": {"v": "feat1"}}]}
    c_feat1 = commit_changes(session, "p1", ds.id, "feature", "Feat commit 1", changes=feat_changes1)

    lca = find_lca(session, ds.id, c_base.id, c_feat1.id)
    assert lca == c_base.id

    # Fast-forward feature into main
    ff_commit = merge_branches(session, "p1", ds.id, "feature", "main")
    assert ff_commit.id == c_feat1.id

    # Now diverge branches:
    # Main updates k_conflict to "val_main"
    c_main2 = commit_changes(
        session, "p1", ds.id, "main", "Main update",
        changes={"upsert": [{"item_key": "k_conflict", "input": {"v": "val_main"}}]}
    )
    # Feature updates k_conflict to "val_feat" (CONFLICT) and adds k_feat2
    c_feat2 = commit_changes(
        session, "p1", ds.id, "feature", "Feat update",
        changes={
            "upsert": [
                {"item_key": "k_conflict", "input": {"v": "val_feat"}},
                {"item_key": "k_feat2", "input": {"v": "feat2"}},
            ]
        }
    )

    # 3-way merge with conflict_strategy="fail" must raise MergeConflictError
    with pytest.raises(MergeConflictError) as exc_info:
        merge_branches(session, "p1", ds.id, "feature", "main", conflict_strategy="fail")
    assert len(exc_info.value.conflicts) == 1
    assert exc_info.value.conflicts[0]["item_key"] == "k_conflict"

    # 3-way merge with conflict_strategy="theirs" takes feature's version
    m_theirs = merge_branches(session, "p1", ds.id, "feature", "main", conflict_strategy="theirs")
    assert m_theirs.parent_commit_id == c_main2.id
    assert m_theirs.second_parent_commit_id == c_feat2.id

    # Verify item contents after theirs merge
    diff_from_base = diff_commits(session, "p1", ds.id, c_base.id, m_theirs.id)
    assert "k_feat2" in diff_from_base["added"]
    assert "k_conflict" in diff_from_base["modified"]


def test_revert_branch_engine(session):
    ds = Dataset(project_id="p1", name="revert_ds")
    session.add(ds)
    session.flush()

    c1 = commit_changes(
        session, "p1", ds.id, "main", "Commit 1",
        changes={"upsert": [{"item_key": "item1", "input": {"val": 1}}]}
    )
    c2 = commit_changes(
        session, "p1", ds.id, "main", "Commit 2",
        changes={"upsert": [{"item_key": "item2", "input": {"val": 2}}]}
    )
    assert c2.item_count == 2

    # Revert to c1
    c3 = revert_branch(session, "p1", ds.id, "main", c1.id)
    assert c3.item_count == 1
    assert c3.tree_hash == c1.tree_hash
    assert c3.stats["removed"] == 1
    assert c3.stats["reverted_to"] == c1.id


# --- REST API Integration Tests ------------------------------------------


def test_api_branches_commits_diff_merge_revert(client_env):
    client, auth, _ = client_env

    # 1. Create dataset
    ds_resp = client.post("/api/v1/datasets", json={"name": "sota_dataset", "description": "test"}, headers=auth)
    assert ds_resp.status_code == 201
    ds_id = ds_resp.json()["id"]

    # 2. Add item with multimodal media
    media_item = {
        "input": {"question": "What is in the image?"},
        "expected_output": "A golden retriever",
        "media": [
            {
                "kind": "image",
                "url": "https://storage.matrix.local/images/dog.jpg",
                "mime": "image/jpeg",
                "width": 1024,
                "height": 768,
            },
            {
                "kind": "bbox",
                "boxes": [{"x": 100, "y": 150, "w": 200, "h": 300, "label": "dog"}],
            },
        ],
    }
    item_resp = client.post(f"/api/v1/datasets/{ds_id}/items", json=media_item, headers=auth)
    assert item_resp.status_code == 201
    created_item = item_resp.json()
    assert "media" in created_item
    assert len(created_item["media"]) == 2
    assert created_item["media"][0]["kind"] == "image"

    # Verify GET /items includes media
    list_items_resp = client.get(f"/api/v1/datasets/{ds_id}/items", headers=auth)
    assert list_items_resp.status_code == 200
    assert len(list_items_resp.json()["items"]) == 1
    assert list_items_resp.json()["items"][0]["media"][0]["kind"] == "image"

    # 3. Create initial commit from working tree on main
    commit1_resp = client.post(
        f"/api/v1/datasets/{ds_id}/commits",
        json={"branch": "main", "message": "Initial working tree snapshot", "snapshot_from_working_tree": True},
        headers=auth,
    )
    assert commit1_resp.status_code == 201
    c1 = commit1_resp.json()
    assert c1["item_count"] == 1
    assert c1["stats"]["added"] == 1

    # 4. List branches
    br_resp = client.get(f"/api/v1/datasets/{ds_id}/branches", headers=auth)
    assert br_resp.status_code == 200
    branches = br_resp.json()["items"]
    assert len(branches) == 1
    assert branches[0]["name"] == "main"
    assert branches[0]["head_commit_id"] == c1["id"]

    # 5. Create branch 'experiment'
    br2_resp = client.post(
        f"/api/v1/datasets/{ds_id}/branches",
        json={"name": "experiment", "from_branch": "main"},
        headers=auth,
    )
    assert br2_resp.status_code == 201
    assert br2_resp.json()["head_commit_id"] == c1["id"]

    # 6. Make commit on 'experiment' branch
    commit_exp_resp = client.post(
        f"/api/v1/datasets/{ds_id}/commits",
        json={
            "branch": "experiment",
            "message": "Add second sample",
            "changes": {
                "upsert": [
                    {
                        "item_key": "item_exp_2",
                        "input": {"question": "Describe audio"},
                        "media": [{"kind": "audio", "url": "https://storage.matrix.local/audio/sample.mp3"}],
                    }
                ]
            },
        },
        headers=auth,
    )
    assert commit_exp_resp.status_code == 201
    c_exp = commit_exp_resp.json()
    assert c_exp["item_count"] == 2

    # 7. List commit items
    ci_resp = client.get(f"/api/v1/datasets/{ds_id}/commits/{c_exp['id']}/items", headers=auth)
    assert ci_resp.status_code == 200
    ci_items = ci_resp.json()["items"]
    assert len(ci_items) == 2
    keys = {i["item_key"] for i in ci_items}
    assert "item_exp_2" in keys

    # 8. Diff between main and experiment
    diff_resp = client.get(f"/api/v1/datasets/{ds_id}/diff?base=main&head=experiment", headers=auth)
    assert diff_resp.status_code == 200
    diff_data = diff_resp.json()
    assert diff_data["added"] == ["item_exp_2"]
    assert diff_data["stats"]["added"] == 1

    # 9. Diverge main and test 3-way merge conflict
    client.post(
        f"/api/v1/datasets/{ds_id}/commits",
        json={
            "branch": "main",
            "message": "Add conflicting item with same key but different content",
            "changes": {
                "upsert": [
                    {
                        "item_key": "item_exp_2",
                        "input": {"question": "Conflicting content on main"},
                    }
                ]
            },
        },
        headers=auth,
    )

    # Merge conflict (fail strategy)
    conflict_resp = client.post(
        f"/api/v1/datasets/{ds_id}/merge",
        json={"source_branch": "experiment", "target_branch": "main", "conflict_strategy": "fail"},
        headers=auth,
    )
    assert conflict_resp.status_code == 409
    assert "conflicts" in conflict_resp.json()["detail"]
    assert conflict_resp.json()["detail"]["conflicts"][0]["item_key"] == "item_exp_2"

    # Merge with conflict_strategy="ours"
    merge_resp = client.post(
        f"/api/v1/datasets/{ds_id}/merge",
        json={"source_branch": "experiment", "target_branch": "main", "conflict_strategy": "ours"},
        headers=auth,
    )
    assert merge_resp.status_code == 200
    merge_commit = merge_resp.json()
    assert merge_commit["second_parent_commit_id"] == c_exp["id"]

    # 10. Revert main to c1
    revert_resp = client.post(
        f"/api/v1/datasets/{ds_id}/revert",
        json={"branch": "main", "target_commit_id": c1["id"]},
        headers=auth,
    )
    assert revert_resp.status_code == 201
    rev_commit = revert_resp.json()
    assert rev_commit["tree_hash"] == c1["tree_hash"]
    assert rev_commit["item_count"] == 1


# --- RBAC and Multi-Tenant Isolation Tests --------------------------------


def test_rbac_and_multitenant_isolation(client_env):
    client, auth_alpha, auth_beta = client_env

    # Setup dataset in Alpha
    ds_resp = client.post("/api/v1/datasets", json={"name": "alpha_ds"}, headers=auth_alpha)
    assert ds_resp.status_code == 201
    ds_id = ds_resp.json()["id"]

    c1 = client.post(
        f"/api/v1/datasets/{ds_id}/commits",
        json={"branch": "main", "message": "Alpha commit 1", "changes": {"upsert": [{"item_key": "k1", "input": {"a": 1}}]}},
        headers=auth_alpha,
    ).json()

    # 1. Multi-tenant isolation (Anti-IDOR)
    # Tenant Beta cannot access Alpha's branches, commits, diff, or items
    assert client.get(f"/api/v1/datasets/{ds_id}/branches", headers=auth_beta).status_code == 404
    assert client.get(f"/api/v1/datasets/{ds_id}/commits", headers=auth_beta).status_code == 404
    assert client.get(f"/api/v1/datasets/{ds_id}/commits/{c1['id']}/items", headers=auth_beta).status_code == 404
    assert client.get(f"/api/v1/datasets/{ds_id}/diff?base=main&head={c1['id']}", headers=auth_beta).status_code == 404
    assert client.post(
        f"/api/v1/datasets/{ds_id}/branches",
        json={"name": "hacked"},
        headers=auth_beta,
    ).status_code == 404
    assert client.post(
        f"/api/v1/datasets/{ds_id}/commits",
        json={"branch": "main", "message": "Infiltrate"},
        headers=auth_beta,
    ).status_code == 404

    # 2. RBAC: User with 'viewer' role can read but cannot mutate
    # Register and invite a viewer to Alpha
    user_resp = client.post(
        "/api/v1/auth/register",
        json={"email": "viewer@alpha.com", "password": "password123", "name": "Alpha Viewer"},
    )
    assert user_resp.status_code == 201
    viewer_token = user_resp.json()["token"]

    # Get Alpha project ID
    me_alpha = client.get("/api/v1/auth/me", headers=auth_alpha).json()
    alpha_proj_id = me_alpha["current_project"]["id"]

    # Add viewer to Alpha project
    client.post(
        f"/api/v1/projects/{alpha_proj_id}/members",
        json={"email": "viewer@alpha.com", "role": "viewer"},
        headers=auth_alpha,
    )

    viewer_auth = {"Authorization": f"Bearer {viewer_token}", "x-matrix-project-id": alpha_proj_id}

    # Viewer can GET branches, commits, items
    assert client.get(f"/api/v1/datasets/{ds_id}/branches", headers=viewer_auth).status_code == 200
    assert client.get(f"/api/v1/datasets/{ds_id}/commits", headers=viewer_auth).status_code == 200
    assert client.get(f"/api/v1/datasets/{ds_id}/commits/{c1['id']}/items", headers=viewer_auth).status_code == 200

    # Viewer CANNOT create branch (403)
    post_br = client.post(
        f"/api/v1/datasets/{ds_id}/branches",
        json={"name": "viewer_branch"},
        headers=viewer_auth,
    )
    assert post_br.status_code == 403

    # Viewer CANNOT create commit (403)
    post_com = client.post(
        f"/api/v1/datasets/{ds_id}/commits",
        json={"branch": "main", "message": "viewer commit"},
        headers=viewer_auth,
    )
    assert post_com.status_code == 403

    # Viewer CANNOT merge (403)
    post_merge = client.post(
        f"/api/v1/datasets/{ds_id}/merge",
        json={"source_branch": "main", "target_branch": "main"},
        headers=viewer_auth,
    )
    assert post_merge.status_code == 403

    # Viewer CANNOT revert (403)
    post_rev = client.post(
        f"/api/v1/datasets/{ds_id}/revert",
        json={"branch": "main", "target_commit_id": c1["id"]},
        headers=viewer_auth,
    )
    assert post_rev.status_code == 403
