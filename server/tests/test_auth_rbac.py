import pytest
from fastapi.testclient import TestClient

from matrix_server.app import create_app
from matrix_server.auth import create_jwt, decode_jwt, hash_password, verify_password
from matrix_server.config import Settings


@pytest.fixture
def app(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/auth_test.db", span_store="memory")
    return create_app(settings)


@pytest.fixture
def client(app):
    return TestClient(app)


def test_password_hashing():
    pwd = "secret-password-123"
    hashed = hash_password(pwd)
    assert hashed != pwd
    assert hashed.startswith("pbkdf2:sha256:")
    assert verify_password(pwd, hashed)
    assert not verify_password("wrong-password", hashed)
    assert not verify_password(pwd, "invalid-hash-string")


def test_jwt_create_and_decode():
    secret = "test-secret-key"
    token = create_jwt({"sub": "user-1", "role": "admin"}, secret, expires_in_seconds=60)
    decoded = decode_jwt(token, secret)
    assert decoded is not None
    assert decoded["sub"] == "user-1"
    assert decoded["role"] == "admin"

    # Invalid secret
    assert decode_jwt(token, "different-secret") is None

    # Tampered token
    tampered = token[:-3] + "abc"
    assert decode_jwt(tampered, secret) is None

    # Expired token
    expired_token = create_jwt({"sub": "user-2"}, secret, expires_in_seconds=-10)
    assert decode_jwt(expired_token, secret) is None


def test_register_and_login_flow(client):
    # 1. Register
    reg_resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "alice-secure-password",
            "name": "Alice Developer",
            "project_name": "Alice LLM Project",
        },
    )
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert "token" in reg_data
    assert reg_data["user"]["email"] == "alice@example.com"
    assert reg_data["user"]["name"] == "Alice Developer"
    assert reg_data["project"]["name"] == "Alice LLM Project"
    assert reg_data["project"]["role"] == "admin"

    token = reg_data["token"]
    project_id = reg_data["project"]["id"]

    # 2. Duplicate registration returns 409
    dup_resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@example.com",
            "password": "another-password",
            "name": "Alice 2",
        },
    )
    assert dup_resp.status_code == 409

    # 3. Login
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "alice-secure-password"},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert "token" in login_data
    assert len(login_data["projects"]) == 1
    assert login_data["projects"][0]["id"] == project_id

    # 4. Login with invalid password
    bad_login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "wrong-password"},
    )
    assert bad_login.status_code == 401

    # 5. GET /api/v1/auth/me
    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["user"]["email"] == "alice@example.com"
    assert me_data["current_project"]["id"] == project_id
    assert me_data["current_project"]["role"] == "admin"


def test_rbac_and_member_management(client):
    # Register Alice (admin)
    alice_reg = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@corp.com", "password": "password123", "name": "Alice Admin"},
    ).json()
    alice_token = alice_reg["token"]
    project_id = alice_reg["project"]["id"]

    # Register Bob (will be invited as viewer)
    bob_reg = client.post(
        "/api/v1/auth/register",
        json={"email": "bob@corp.com", "password": "password123", "name": "Bob Viewer"},
    ).json()
    bob_token = bob_reg["token"]

    # Bob tries to access Alice's project -> 403
    bob_me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {bob_token}", "x-matrix-project-id": project_id},
    )
    assert bob_me.status_code == 403

    # Alice adds Bob as viewer
    add_resp = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"email": "bob@corp.com", "role": "viewer"},
    )
    assert add_resp.status_code == 201
    assert "id" in add_resp.json()

    # List members
    list_resp = client.get(
        f"/api/v1/projects/{project_id}/members",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert list_resp.status_code == 200
    members = list_resp.json()["members"]
    assert len(members) == 2
    emails = {m["email"]: m["role"] for m in members}
    assert emails["alice@corp.com"] == "admin"
    assert emails["bob@corp.com"] == "viewer"

    # Bob now can access the project as viewer
    bob_access = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {bob_token}", "x-matrix-project-id": project_id},
    )
    assert bob_access.status_code == 200
    assert bob_access.json()["current_project"]["role"] == "viewer"

    # Bob (viewer) cannot add members (requires admin) -> 403
    bob_add = client.post(
        f"/api/v1/projects/{project_id}/members",
        headers={"Authorization": f"Bearer {bob_token}", "x-matrix-project-id": project_id},
        json={"email": "eve@corp.com", "role": "viewer"},
    )
    assert bob_add.status_code == 403

    # Alice promotes Bob to member
    promote_resp = client.patch(
        f"/api/v1/projects/{project_id}/members/{bob_reg['user']['id']}",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"role": "member"},
    )
    assert promote_resp.status_code == 200
    assert promote_resp.json()["role"] == "member"

    # Alice cannot demote herself (sole admin)
    demote_self = client.patch(
        f"/api/v1/projects/{project_id}/members/{alice_reg['user']['id']}",
        headers={"Authorization": f"Bearer {alice_token}"},
        json={"role": "member"},
    )
    assert demote_self.status_code == 400

    # Alice removes Bob
    del_resp = client.delete(
        f"/api/v1/projects/{project_id}/members/{bob_reg['user']['id']}",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert del_resp.status_code == 204
