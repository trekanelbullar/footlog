"""W1〜W7b の API テスト（TestClient、実DB）。設計書 §1.3・§2.1。"""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import psycopg
from fastapi.testclient import TestClient

from ai_hackathon_team_a.api import app
from ai_hackathon_team_a.api.deps import get_auth_admin_transport

AuthHeaders = Callable[..., dict[str, str]]


def _insert_project(conn: psycopg.Connection, *, name: str = "P", goal: str = "G") -> UUID:
    row = conn.execute(
        "INSERT INTO projects (name, goal_description) VALUES (%s, %s) RETURNING id",
        (name, goal),
    ).fetchone()
    conn.commit()
    return row[0]


def _insert_member(
    conn: psycopg.Connection, *, project_id: UUID, user_id: UUID, email: str, role: str
) -> None:
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, email, role) VALUES (%s, %s, %s, %s)",
        (project_id, user_id, email, role),
    )
    conn.commit()


def _mock_auth_admin_transport(users: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"users": users})

    return httpx.MockTransport(handler)


# ---- W1 ----------------------------------------------------------------


def test_list_my_projects_returns_only_own_memberships(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn, name="mine")
        _insert_member(
            conn, project_id=pid, user_id=user_id, email="me@example.test", role="manager"
        )
        _insert_project(conn, name="not-mine")

    response = api_client.get("/internal/me/projects", headers=auth_headers(user_id))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["project_id"] == str(pid)
    assert body[0]["role"] == "manager"
    assert body[0]["latest_version_no"] is None


# ---- W2 ------------------------------------------------------------------


def test_create_project_makes_creator_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()

    response = api_client.post(
        "/internal/projects",
        headers=auth_headers(user_id, email="creator@example.test"),
        json={"name": "新しいプロジェクト", "goal_description": "ゴール"},
    )

    assert response.status_code == 200
    project_id = response.json()["project_id"]

    with psycopg.connect(migrated_database_url) as conn:
        row = conn.execute(
            "SELECT role, email FROM project_members WHERE project_id = %s AND user_id = %s",
            (project_id, str(user_id)),
        ).fetchone()
    assert row == ("manager", "creator@example.test")


# ---- W3 ------------------------------------------------------------------


def test_get_project_returns_404_for_non_member(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)

    response = api_client.get(f"/internal/projects/{pid}", headers=auth_headers())

    assert response.status_code == 404


def test_get_project_excluded_summary_only_for_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )
        _insert_member(
            conn, project_id=pid, user_id=member_id, email="u@example.test", role="member"
        )
        conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, recorded_at, "
            "visibility, is_excluded, exclude_reason) VALUES "
            "(%s, 1, 'conversation', %s, now(), 'all', true, 'irrelevant')",
            (pid, manager_id),
        )
        conn.commit()

    manager_resp = api_client.get(f"/internal/projects/{pid}", headers=auth_headers(manager_id))
    member_resp = api_client.get(f"/internal/projects/{pid}", headers=auth_headers(member_id))

    assert manager_resp.json()["excluded_summary"] == {
        "count": 1,
        "by_reason": {"private": 0, "confidential": 0, "irrelevant": 1, "other": 0},
    }
    assert member_resp.json()["excluded_summary"] is None


# ---- W4 ------------------------------------------------------------------


def test_update_project_requires_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=member_id, email="u@example.test", role="member"
        )

    response = api_client.patch(
        f"/internal/projects/{pid}", headers=auth_headers(member_id), json={"name": "新名"}
    )

    assert response.status_code == 403


def test_update_project_succeeds_for_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )

    response = api_client.patch(
        f"/internal/projects/{pid}", headers=auth_headers(manager_id), json={"name": "新名"}
    )

    assert response.status_code == 200
    assert response.json()["project"]["name"] == "新名"


# ---- W5・W6 ---------------------------------------------------------------


def test_add_member_requires_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=member_id, email="u@example.test", role="member"
        )

    response = api_client.post(
        f"/internal/projects/{pid}/members",
        headers=auth_headers(member_id),
        json={"email": "new@example.test", "role": "member"},
    )

    assert response.status_code == 403


def test_add_member_unknown_email_returns_404(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )

    app.dependency_overrides[get_auth_admin_transport] = lambda: _mock_auth_admin_transport([])
    try:
        response = api_client.post(
            f"/internal/projects/{pid}/members",
            headers=auth_headers(manager_id),
            json={"email": "nobody@example.test", "role": "member"},
        )
    finally:
        app.dependency_overrides.pop(get_auth_admin_transport, None)

    assert response.status_code == 404
    assert response.json()["error"] == "user_not_found"


def test_add_member_success_and_epoch_bump(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    new_user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )

    app.dependency_overrides[get_auth_admin_transport] = lambda: _mock_auth_admin_transport(
        [{"id": str(new_user_id), "email": "new@example.test"}]
    )
    try:
        response = api_client.post(
            f"/internal/projects/{pid}/members",
            headers=auth_headers(manager_id),
            json={"email": "new@example.test", "role": "member"},
        )
    finally:
        app.dependency_overrides.pop(get_auth_admin_transport, None)

    assert response.status_code == 200
    assert response.json() == {"user_id": str(new_user_id), "role": "member"}

    with psycopg.connect(migrated_database_url) as conn:
        epoch, needs_rebuild = conn.execute(
            "SELECT visibility_epoch, needs_rebuild FROM projects WHERE id = %s", (pid,)
        ).fetchone()
    assert epoch == 1
    assert needs_rebuild is True


def test_add_member_already_member_conflict(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, existing_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )
        _insert_member(
            conn, project_id=pid, user_id=existing_id, email="e@example.test", role="member"
        )

    app.dependency_overrides[get_auth_admin_transport] = lambda: _mock_auth_admin_transport(
        [{"id": str(existing_id), "email": "e@example.test"}]
    )
    try:
        response = api_client.post(
            f"/internal/projects/{pid}/members",
            headers=auth_headers(manager_id),
            json={"email": "e@example.test", "role": "manager"},
        )
    finally:
        app.dependency_overrides.pop(get_auth_admin_transport, None)

    assert response.status_code == 409
    assert response.json()["error"] == "already_member"


# ---- W7・W7b ---------------------------------------------------------------


def test_update_member_role_requires_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id, other_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=member_id, email="a@example.test", role="member"
        )
        _insert_member(
            conn, project_id=pid, user_id=other_id, email="b@example.test", role="member"
        )

    response = api_client.patch(
        f"/internal/projects/{pid}/members/{other_id}",
        headers=auth_headers(member_id),
        json={"role": "manager"},
    )

    assert response.status_code == 403


def test_update_member_notify_settings_self_allowed(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=member_id, email="a@example.test", role="member"
        )

    response = api_client.patch(
        f"/internal/projects/{pid}/members/{member_id}",
        headers=auth_headers(member_id),
        json={"notify_on_no_progress": False},
    )

    assert response.status_code == 200
    assert response.json()["member"]["notify_on_no_progress"] is False


def test_update_member_last_manager_demotion_is_conflict(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )

    response = api_client.patch(
        f"/internal/projects/{pid}/members/{manager_id}",
        headers=auth_headers(manager_id),
        json={"role": "member"},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "last_manager"


def test_delete_member_last_manager_is_conflict(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )

    response = api_client.delete(
        f"/internal/projects/{pid}/members/{manager_id}", headers=auth_headers(manager_id)
    )

    assert response.status_code == 409
    assert response.json()["error"] == "last_manager"


def test_delete_member_succeeds_and_then_member_gets_404(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )
        _insert_member(
            conn, project_id=pid, user_id=member_id, email="u@example.test", role="member"
        )

    response = api_client.delete(
        f"/internal/projects/{pid}/members/{member_id}", headers=auth_headers(manager_id)
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # 削除された直後から、その人の全 API が404になる【A-X1】。
    follow_up = api_client.get(f"/internal/projects/{pid}", headers=auth_headers(member_id))
    assert follow_up.status_code == 404


def test_other_projects_pid_does_not_leak_membership(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        my_project = _insert_project(conn, name="mine")
        other_project = _insert_project(conn, name="other")
        _insert_member(
            conn, project_id=my_project, user_id=user_id, email="u@example.test", role="manager"
        )

    response = api_client.get(f"/internal/projects/{other_project}", headers=auth_headers(user_id))

    assert response.status_code == 404
