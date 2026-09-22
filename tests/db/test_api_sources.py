"""W8〜W10・W13 の API テスト（TestClient、実DB）。設計書 §1.3・§2.2・§4。"""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from ai_hackathon_team_a.api import app
from ai_hackathon_team_a.api.deps import get_storage_transport

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


def _mock_storage_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/object/sign/" in request.url.path:
            return httpx.Response(200, json={"signedURL": f"{request.url.path}?token=fake"})
        return httpx.Response(200, json={"Key": "sources/fake"})

    return httpx.MockTransport(handler)


def _with_storage_transport() -> None:
    app.dependency_overrides[get_storage_transport] = _mock_storage_transport


def _without_storage_transport() -> None:
    app.dependency_overrides.pop(get_storage_transport, None)


# ---- W8 --------------------------------------------------------------------


def test_create_conversation_source_requires_membership(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)

    response = api_client.post(
        f"/internal/projects/{pid}/sources/conversation",
        headers=auth_headers(),
        json={"text": "You: hi", "visibility": "all"},
    )

    assert response.status_code == 404


def test_create_conversation_source_success(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, email="u@example.test", role="member")

    response = api_client.post(
        f"/internal/projects/{pid}/sources/conversation",
        headers=auth_headers(user_id),
        json={"text": "You: 予算はいくらですか\nChatGPT: 5万円です", "visibility": "all"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version_no"] == 1
    assert body["segment_count"] == 2
    assert body["label_prefix"].startswith("S")


def test_create_conversation_source_rejects_too_long_paste(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, email="u@example.test", role="member")

    response = api_client.post(
        f"/internal/projects/{pid}/sources/conversation",
        headers=auth_headers(user_id),
        json={"text": "x" * 200_001, "visibility": "all"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "text_too_long"


# ---- W9 --------------------------------------------------------------------


def test_create_file_source_rejects_xlsm(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, email="u@example.test", role="member")

    response = api_client.post(
        f"/internal/projects/{pid}/sources/file",
        headers=auth_headers(user_id),
        files={"file": ("macro.xlsm", b"dummy", "application/octet-stream")},
        data={"visibility": "all"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_file_type"


@pytest.mark.parametrize("name", ["old.xls", "old.doc", "old.ppt", "macro.xlsm"])
def test_create_file_source_asks_to_resave_old_formats(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders, name: str
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, email="u@example.test", role="member")

    response = api_client.post(
        f"/internal/projects/{pid}/sources/file",
        headers=auth_headers(user_id),
        files={"file": (name, b"dummy", "application/octet-stream")},
        data={"visibility": "all"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_file_type"
    assert "新しい形式（.xlsx / .docx / .pptx）で保存し直してください" in response.json()["message"]


def test_create_file_source_rejects_oversized(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, email="u@example.test", role="member")

    oversized = b"a" * (10 * 1024 * 1024 + 1)
    response = api_client.post(
        f"/internal/projects/{pid}/sources/file",
        headers=auth_headers(user_id),
        files={"file": ("big.txt", oversized, "text/plain")},
        data={"visibility": "all"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "file_too_large"


def test_create_file_source_success(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, email="u@example.test", role="member")

    _with_storage_transport()
    try:
        response = api_client.post(
            f"/internal/projects/{pid}/sources/file",
            headers=auth_headers(user_id),
            files={"file": ("notes.txt", b"paragraph one\n\nparagraph two", "text/plain")},
            data={"visibility": "all"},
        )
    finally:
        _without_storage_transport()

    assert response.status_code == 200
    body = response.json()
    assert body["version_no"] == 1
    assert body["segment_count"] == 2
    assert body["new_segment_count"] == 2


# ---- W10・W13 ---------------------------------------------------------------


def test_list_sources_member_cannot_see_others_managers_only_source(
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
            "visibility) VALUES (%s, 1, 'conversation', %s, now(), 'managers_only')",
            (pid, manager_id),
        )
        conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, recorded_at, "
            "visibility) VALUES (%s, 2, 'conversation', %s, now(), 'all')",
            (pid, manager_id),
        )
        conn.commit()

    member_resp = api_client.get(
        f"/internal/projects/{pid}/sources", headers=auth_headers(member_id)
    )
    manager_resp = api_client.get(
        f"/internal/projects/{pid}/sources", headers=auth_headers(manager_id)
    )

    member_source_nos = {row["source_no"] for row in member_resp.json()}
    manager_source_nos = {row["source_no"] for row in manager_resp.json()}
    assert member_source_nos == {2}
    assert manager_source_nos == {1, 2}


def test_download_url_member_gets_404_for_others_managers_only_source(
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
        source_row = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, filename, uploaded_by, "
            "recorded_at, visibility) VALUES (%s, 1, 'file', 'secret.txt', %s, now(), "
            "'managers_only') RETURNING id",
            (pid, manager_id),
        ).fetchone()
        version_row = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text, "
            "storage_path) VALUES (%s, 1, 'h', 't', 'projects/x/sources/1/v1/secret.txt') "
            "RETURNING id",
            (source_row[0],),
        ).fetchone()
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_row[0], source_row[0]),
        )
        conn.commit()
        sid = source_row[0]

    response = api_client.get(
        f"/internal/sources/{sid}/download-url", headers=auth_headers(member_id)
    )

    assert response.status_code == 404


def test_download_url_not_found_for_other_projects_sid(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        my_project = _insert_project(conn)
        other_project = _insert_project(conn)
        _insert_member(
            conn, project_id=my_project, user_id=user_id, email="u@example.test", role="manager"
        )
        source_row = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, filename, uploaded_by, "
            "recorded_at, visibility) VALUES "
            "(%s, 1, 'file', 'f.txt', %s, now(), 'all') RETURNING id",
            (other_project, uuid4()),
        ).fetchone()
        conn.commit()
        sid = source_row[0]

    response = api_client.get(
        f"/internal/sources/{sid}/download-url", headers=auth_headers(user_id)
    )

    assert response.status_code == 404


def test_download_url_success_for_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(
            conn, project_id=pid, user_id=manager_id, email="m@example.test", role="manager"
        )
        source_row = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, filename, uploaded_by, "
            "recorded_at, visibility) VALUES "
            "(%s, 1, 'file', 'f.txt', %s, now(), 'all') RETURNING id",
            (pid, manager_id),
        ).fetchone()
        version_row = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text, "
            "storage_path) VALUES (%s, 1, 'h', 't', 'projects/x/sources/1/v1/f.txt') RETURNING id",
            (source_row[0],),
        ).fetchone()
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_row[0], source_row[0]),
        )
        conn.commit()
        sid = source_row[0]

    _with_storage_transport()
    try:
        response = api_client.get(
            f"/internal/sources/{sid}/download-url", headers=auth_headers(manager_id)
        )
    finally:
        _without_storage_transport()

    assert response.status_code == 200
    body = response.json()
    assert body["url"].endswith("token=fake")
    assert "expires_at" in body
