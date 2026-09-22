"""I5：除外は件数として必ず管理者に見える（設計書 §5.5・§5.6、【C-4】）。

除外の直後、実行前でも manager の W3 に件数と内訳が出ること。最後の manager の
降格・削除が409になること。manager が除外した member のソースを本人が戻せない
こと。本人が除外したものは本人が戻せること。
"""

from collections.abc import Callable
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

AuthHeaders = Callable[..., dict[str, str]]


def _insert_project(conn: psycopg.Connection) -> UUID:
    row = conn.execute(
        "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
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


def _insert_source(
    conn: psycopg.Connection, *, project_id: UUID, source_no: int, uploaded_by: UUID
) -> UUID:
    row = conn.execute(
        "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, recorded_at, "
        "visibility) VALUES (%s, %s, 'conversation', %s, now(), 'all') RETURNING id",
        (project_id, source_no, uploaded_by),
    ).fetchone()
    conn.commit()
    return row[0]


def test_excluded_summary_appears_immediately_before_any_run(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, email="u@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)

    exclude_resp = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(member_id),
        json={"is_excluded": True, "reason": "confidential"},
    )
    assert exclude_resp.status_code == 200

    project_resp = api_client.get(f"/internal/projects/{pid}", headers=auth_headers(manager_id))
    assert project_resp.status_code == 200
    assert project_resp.json()["excluded_summary"] == {
        "count": 1,
        "by_reason": {"private": 0, "confidential": 1, "irrelevant": 0, "other": 0},
    }


def test_last_manager_cannot_be_demoted_or_deleted(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")

    demote_resp = api_client.patch(
        f"/internal/projects/{pid}/members/{manager_id}",
        headers=auth_headers(manager_id),
        json={"role": "member"},
    )
    assert demote_resp.status_code == 409
    assert demote_resp.json()["error"] == "last_manager"

    delete_resp = api_client.delete(
        f"/internal/projects/{pid}/members/{manager_id}", headers=auth_headers(manager_id)
    )
    assert delete_resp.status_code == 409
    assert delete_resp.json()["error"] == "last_manager"


def test_member_cannot_revert_exclusion_made_by_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, email="u@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)

    exclude_resp = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(manager_id),
        json={"is_excluded": True, "reason": "private"},
    )
    assert exclude_resp.status_code == 200

    revert_resp = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(member_id),
        json={"is_excluded": False, "reason": "other"},
    )
    assert revert_resp.status_code == 403


def test_member_can_revert_own_exclusion(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=member_id, email="u@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)

    exclude_resp = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(member_id),
        json={"is_excluded": True, "reason": "private"},
    )
    assert exclude_resp.status_code == 200

    revert_resp = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(member_id),
        json={"is_excluded": False, "reason": "other"},
    )
    assert revert_resp.status_code == 200
    assert revert_resp.json()["source"]["is_excluded"] is False
