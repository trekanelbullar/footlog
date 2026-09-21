"""AD-12：W25 出来事の検索（GET /internal/projects/{pid}/events/search?q=...）。"""

from collections.abc import Callable
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

AuthHeaders = Callable[..., dict[str, str]]


def _insert_project(conn: psycopg.Connection) -> UUID:
    row = conn.execute(
        "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
    ).fetchone()
    return row[0]


def _insert_member(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID, role: str) -> None:
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, email, role) VALUES (%s, %s, %s, %s)",
        (project_id, user_id, f"{user_id}@example.test", role),
    )


def _insert_run(conn: psycopg.Connection, *, project_id: UUID) -> UUID:
    return conn.execute(
        "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'done') RETURNING id",
        (project_id,),
    ).fetchone()[0]


def _insert_source(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    source_no: int,
    uploaded_by: UUID,
    visibility: str = "all",
    is_excluded: bool = False,
) -> UUID:
    source_id = conn.execute(
        "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, recorded_at, "
        "visibility, is_excluded, next_seq) VALUES (%s, %s, 'conversation', %s, now(), %s, %s, 2) "
        "RETURNING id",
        (project_id, source_no, uploaded_by, visibility, is_excluded),
    ).fetchone()[0]
    version_id = conn.execute(
        "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
        "VALUES (%s, 1, 'h', 't') RETURNING id",
        (source_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
        (version_id, source_id),
    )
    return version_id


def _insert_segment(
    conn: psycopg.Connection, *, project_id: UUID, version_id: UUID, label: str, text: str
) -> None:
    seq = int(label.rsplit("-", 1)[1])
    conn.execute(
        "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
        "text, is_new, consumed) VALUES (%s, %s, %s, %s, 'user', %s, true, true)",
        (project_id, version_id, label, seq, text),
    )


def _insert_event(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    event_no: int,
    kind: str,
    summary: str,
    reason: str | None,
    segment_ids: list[str],
    visibility_at_creation: str = "all",
    partition: str = "all",
) -> None:
    conn.execute(
        "INSERT INTO events (project_id, run_id, event_no, kind, summary, reason, occurred_at, "
        "segment_ids, visibility_at_creation, partition) VALUES "
        "(%s, %s, %s, %s, %s, %s, now(), %s, %s, %s)",
        (
            project_id,
            run_id,
            event_no,
            kind,
            summary,
            reason,
            segment_ids,
            visibility_at_creation,
            partition,
        ),
    )


def test_query_too_short_returns_400(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, role="member")
        conn.commit()

    response = api_client.get(
        f"/internal/projects/{pid}/events/search", params={"q": "a"}, headers=auth_headers(user_id)
    )

    assert response.status_code == 400
    assert response.json()["error"] == "query_too_short"


def test_other_projects_pid_returns_404(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        mine = _insert_project(conn)
        other = _insert_project(conn)
        _insert_member(conn, project_id=mine, user_id=user_id, role="member")
        conn.commit()

    response = api_client.get(
        f"/internal/projects/{other}/events/search",
        params={"q": "ab"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 404


def test_search_matches_summary_and_reason_partial_case_insensitive(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, role="manager")
        run_id = _insert_run(conn, project_id=pid)
        version_id = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=user_id)
        _insert_segment(
            conn, project_id=pid, version_id=version_id, label="S1-1", text="Resendを選んだ理由"
        )
        _insert_event(
            conn,
            project_id=pid,
            run_id=run_id,
            event_no=1,
            kind="decision",
            summary="配信基盤はResendを採用",
            reason="コストが安いから",
            segment_ids=["S1-1"],
        )
        _insert_event(
            conn,
            project_id=pid,
            run_id=run_id,
            event_no=2,
            kind="decision",
            summary="関係の無い決定",
            reason=None,
            segment_ids=[],
        )
        conn.commit()

    response = api_client.get(
        f"/internal/projects/{pid}/events/search",
        params={"q": "resend"},  # 大文字小文字を区別しない
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["event_no"] == 1
    assert body[0]["kind"] == "decision"
    assert body[0]["summary"] == "配信基盤はResendを採用"
    assert body[0]["reason"] == "コストが安いから"
    assert body[0]["evidence"] == [{"label": "S1-1", "text": "Resendを選んだ理由"}]


def test_reason_only_match_is_found(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, role="member")
        run_id = _insert_run(conn, project_id=pid)
        _insert_event(
            conn,
            project_id=pid,
            run_id=run_id,
            event_no=1,
            kind="decision",
            summary="Xを採用",
            reason="ユニークキーワードゼブラ",
            segment_ids=[],
        )
        conn.commit()

    response = api_client.get(
        f"/internal/projects/{pid}/events/search",
        params={"q": "ゼブラ"},
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    assert [row["event_no"] for row in response.json()] == [1]


def test_member_cannot_search_managers_only_events(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, role="member")
        run_id = _insert_run(conn, project_id=pid)
        version_id = _insert_source(
            conn,
            project_id=pid,
            source_no=1,
            uploaded_by=manager_id,
            visibility="managers_only",
        )
        _insert_segment(conn, project_id=pid, version_id=version_id, label="S1-1", text="極秘の話")
        _insert_event(
            conn,
            project_id=pid,
            run_id=run_id,
            event_no=1,
            kind="decision",
            summary="レイオフ計画を承認",
            reason=None,
            segment_ids=["S1-1"],
            visibility_at_creation="managers_only",
            partition="managers",
        )
        conn.commit()

    member_resp = api_client.get(
        f"/internal/projects/{pid}/events/search",
        params={"q": "レイオフ"},
        headers=auth_headers(member_id),
    )
    manager_resp = api_client.get(
        f"/internal/projects/{pid}/events/search",
        params={"q": "レイオフ"},
        headers=auth_headers(manager_id),
    )

    assert member_resp.json() == []
    assert [row["event_no"] for row in manager_resp.json()] == [1]


def test_excluded_source_event_is_hidden_from_everyone(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, role="manager")
        run_id = _insert_run(conn, project_id=pid)
        version_id = _insert_source(
            conn, project_id=pid, source_no=1, uploaded_by=manager_id, is_excluded=True
        )
        _insert_segment(conn, project_id=pid, version_id=version_id, label="S1-1", text="除外済み")
        _insert_event(
            conn,
            project_id=pid,
            run_id=run_id,
            event_no=1,
            kind="decision",
            summary="除外されたソース由来の決定",
            reason=None,
            segment_ids=["S1-1"],
        )
        conn.commit()

    response = api_client.get(
        f"/internal/projects/{pid}/events/search",
        params={"q": "除外"},
        headers=auth_headers(manager_id),
    )

    assert response.json() == []


def test_search_does_not_match_source_segment_body_only(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    """要約・理由に無い語で、区切りの本文にだけある語を検索しても結果は0件（本文は検索対象外）。"""

    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, role="manager")
        run_id = _insert_run(conn, project_id=pid)
        version_id = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=user_id)
        _insert_segment(
            conn,
            project_id=pid,
            version_id=version_id,
            label="S1-1",
            text="本文にだけあるユニーク文字列パイナップル",
        )
        _insert_event(
            conn,
            project_id=pid,
            run_id=run_id,
            event_no=1,
            kind="decision",
            summary="要約には出てこない",
            reason=None,
            segment_ids=["S1-1"],
        )
        conn.commit()

    response = api_client.get(
        f"/internal/projects/{pid}/events/search",
        params={"q": "パイナップル"},
        headers=auth_headers(user_id),
    )

    assert response.json() == []
