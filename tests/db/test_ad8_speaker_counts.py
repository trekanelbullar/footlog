"""AD-8：W8（会話の貼り付け）の応答に ``speaker_counts`` が入ること。"""

from collections.abc import Callable
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

AuthHeaders = Callable[..., dict[str, str]]


def _insert_project_with_member(conn: psycopg.Connection, *, user_id: UUID) -> UUID:
    pid = conn.execute(
        "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, email, role) "
        "VALUES (%s, %s, 'u@example.test', 'member')",
        (pid, user_id),
    )
    conn.commit()
    return pid


def test_speaker_counts_reflects_markers(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project_with_member(conn, user_id=user_id)

    response = api_client.post(
        f"/internal/projects/{pid}/sources/conversation",
        headers=auth_headers(user_id),
        json={
            # 最初の話者の目印より前の「メモ」は、話者の目印が無い区切り（unknown）になる。
            "text": "メモ\n\nYou: 予算はいくらですか\nChatGPT: 5万円です",
            "visibility": "all",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["speaker_counts"] == {"user": 1, "ai": 1, "unknown": 1}


def test_speaker_counts_are_all_unknown_without_markers(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project_with_member(conn, user_id=user_id)

    response = api_client.post(
        f"/internal/projects/{pid}/sources/conversation",
        headers=auth_headers(user_id),
        json={"text": "話者の目印が無いただのメモです。\n\n続きの段落。", "visibility": "all"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["speaker_counts"]["user"] == 0
    assert body["speaker_counts"]["ai"] == 0
    assert body["speaker_counts"]["unknown"] == body["segment_count"]
