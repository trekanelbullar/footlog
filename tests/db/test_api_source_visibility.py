"""W11・W12 の API テスト（TestClient、実DB）。設計書 §1.3・§2.2、追加指示【C-4】。

権限表（可視性の変更・除外・除外の取り消し）の各行と、``source_audit_log`` への
追記、【B-X1】（``managers_only`` → ``all`` での区切りの巻き戻し）を確かめる。
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
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    source_no: int,
    uploaded_by: UUID,
    visibility: str = "all",
    is_excluded: bool = False,
    exclude_reason: str | None = None,
) -> UUID:
    version_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
    row = conn.execute(
        """
        INSERT INTO project_sources
            (project_id, source_no, type, uploaded_by, recorded_at, visibility,
             is_excluded, exclude_reason, next_seq)
        VALUES (%s, %s, 'conversation', %s, now(), %s, %s, %s, 2)
        RETURNING id
        """,
        (project_id, source_no, uploaded_by, visibility, is_excluded, exclude_reason),
    ).fetchone()
    source_id = row[0]
    conn.execute(
        "INSERT INTO source_versions (id, source_id, version_no, content_hash, extracted_text) "
        "VALUES (%s, %s, 1, 'h', 't')",
        (version_id, source_id),
    )
    conn.execute(
        "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
        (version_id, source_id),
    )
    conn.execute(
        "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
        "text, is_new, consumed, carry_count) VALUES (%s, %s, %s, 1, 'user', 'hello', true, "
        "true, 2)",
        (project_id, version_id, f"S{source_no}-1"),
    )
    conn.commit()
    return source_id


def _audit_rows(conn: psycopg.Connection, *, source_id: UUID) -> list[tuple]:
    return conn.execute(
        "SELECT field, old_value, new_value, reason, changed_by FROM source_audit_log "
        "WHERE source_id = %s ORDER BY changed_at",
        (source_id,),
    ).fetchall()


# ---- W11：all → managers_only -----------------------------------------------


def test_visibility_all_to_managers_only_by_registrant_member_succeeds(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=member_id, email="m@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)

    response = api_client.patch(
        f"/internal/sources/{sid}/visibility",
        headers=auth_headers(member_id),
        json={"visibility": "managers_only", "reason": "confidential"},
    )

    assert response.status_code == 200
    assert response.json()["source"]["visibility"] == "managers_only"
    with psycopg.connect(migrated_database_url) as conn:
        rows = _audit_rows(conn, source_id=sid)
    assert rows == [("visibility", "all", "managers_only", "confidential", member_id)]


def test_visibility_all_to_managers_only_by_other_member_forbidden(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    registrant_id, other_member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=registrant_id, email="r@x.test", role="member")
        _insert_member(
            conn, project_id=pid, user_id=other_member_id, email="o@x.test", role="member"
        )
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=registrant_id)

    response = api_client.patch(
        f"/internal/sources/{sid}/visibility",
        headers=auth_headers(other_member_id),
        json={"visibility": "managers_only", "reason": "confidential"},
    )

    assert response.status_code == 403
    with psycopg.connect(migrated_database_url) as conn:
        assert _audit_rows(conn, source_id=sid) == []


def test_visibility_all_to_managers_only_by_manager_succeeds_for_others_source(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, email="u@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)

    response = api_client.patch(
        f"/internal/sources/{sid}/visibility",
        headers=auth_headers(manager_id),
        json={"visibility": "managers_only", "reason": "private"},
    )

    assert response.status_code == 200
    assert response.json()["source"]["visibility"] == "managers_only"


# ---- W11：managers_only → all -----------------------------------------------


def test_visibility_managers_only_to_all_by_registrant_member_forbidden(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=member_id, email="m@x.test", role="member")
        sid = _insert_source(
            conn, project_id=pid, source_no=1, uploaded_by=member_id, visibility="managers_only"
        )

    response = api_client.patch(
        f"/internal/sources/{sid}/visibility",
        headers=auth_headers(member_id),
        json={"visibility": "all", "reason": "other"},
    )

    assert response.status_code == 403
    with psycopg.connect(migrated_database_url) as conn:
        assert _audit_rows(conn, source_id=sid) == []


def test_visibility_managers_only_to_all_by_manager_succeeds_and_resets_segments(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")
        sid = _insert_source(
            conn, project_id=pid, source_no=1, uploaded_by=manager_id, visibility="managers_only"
        )
        before_epoch = conn.execute(
            "SELECT visibility_epoch FROM projects WHERE id = %s", (pid,)
        ).fetchone()[0]

    response = api_client.patch(
        f"/internal/sources/{sid}/visibility",
        headers=auth_headers(manager_id),
        json={"visibility": "all", "reason": "other"},
    )

    assert response.status_code == 200
    assert response.json()["source"]["visibility"] == "all"
    with psycopg.connect(migrated_database_url) as conn:
        rows = _audit_rows(conn, source_id=sid)
        assert rows == [("visibility", "managers_only", "all", "other", manager_id)]

        project_row = conn.execute(
            "SELECT visibility_epoch, needs_rebuild FROM projects WHERE id = %s", (pid,)
        ).fetchone()
        assert project_row[0] == before_epoch + 1
        assert project_row[1] is True

        # 【B-X1】：現在の版の区切りが consumed=false・carry_count=0 に戻る。
        seg = conn.execute(
            "SELECT consumed, carry_count FROM source_segments WHERE label = %s", ("S1-1",)
        ).fetchone()
        assert seg == (False, 0)


def test_visibility_noop_does_not_write_audit_log_or_bump_epoch(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=member_id, email="m@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)
        before_epoch = conn.execute(
            "SELECT visibility_epoch FROM projects WHERE id = %s", (pid,)
        ).fetchone()[0]

    response = api_client.patch(
        f"/internal/sources/{sid}/visibility",
        headers=auth_headers(member_id),
        json={"visibility": "all", "reason": "other"},
    )

    assert response.status_code == 200
    with psycopg.connect(migrated_database_url) as conn:
        assert _audit_rows(conn, source_id=sid) == []
        after_epoch = conn.execute(
            "SELECT visibility_epoch FROM projects WHERE id = %s", (pid,)
        ).fetchone()[0]
    assert after_epoch == before_epoch


def test_visibility_not_found_for_unknown_sid(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    response = api_client.patch(
        f"/internal/sources/{uuid4()}/visibility",
        headers=auth_headers(),
        json={"visibility": "all", "reason": "other"},
    )
    assert response.status_code == 404


def test_visibility_not_found_when_caller_not_a_member(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    owner_id, stranger_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=owner_id, email="o@x.test", role="manager")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=owner_id)

    response = api_client.patch(
        f"/internal/sources/{sid}/visibility",
        headers=auth_headers(stranger_id),
        json={"visibility": "managers_only", "reason": "other"},
    )
    assert response.status_code == 404


# ---- W12：除外 ---------------------------------------------------------------


def test_exclusion_by_registrant_member_succeeds(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=member_id, email="m@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)

    response = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(member_id),
        json={"is_excluded": True, "reason": "irrelevant"},
    )

    assert response.status_code == 200
    body = response.json()["source"]
    assert body["is_excluded"] is True
    assert body["exclude_reason"] == "irrelevant"
    with psycopg.connect(migrated_database_url) as conn:
        rows = _audit_rows(conn, source_id=sid)
    assert rows == [("is_excluded", "false", "true", "irrelevant", member_id)]


def test_exclusion_by_other_member_forbidden(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    registrant_id, other_member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=registrant_id, email="r@x.test", role="member")
        _insert_member(
            conn, project_id=pid, user_id=other_member_id, email="o@x.test", role="member"
        )
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=registrant_id)

    response = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(other_member_id),
        json={"is_excluded": True, "reason": "irrelevant"},
    )

    assert response.status_code == 403


def test_exclusion_by_manager_succeeds_for_others_source(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, email="u@x.test", role="member")
        sid = _insert_source(conn, project_id=pid, source_no=1, uploaded_by=member_id)

    response = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(manager_id),
        json={"is_excluded": True, "reason": "confidential"},
    )

    assert response.status_code == 200


# ---- W12：除外の取り消し【C-4】 ----------------------------------------------


def test_exclusion_revert_by_manager_always_succeeds(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, email="u@x.test", role="member")
        sid = _insert_source(
            conn,
            project_id=pid,
            source_no=1,
            uploaded_by=member_id,
            is_excluded=True,
            exclude_reason="irrelevant",
        )
        # 最後の除外の操作は member 本人によるものにしておく。
        conn.execute(
            "INSERT INTO source_audit_log (source_id, changed_by, field, old_value, "
            "new_value, reason) VALUES (%s, %s, 'is_excluded', 'false', 'true', 'irrelevant')",
            (sid, member_id),
        )
        conn.commit()

    response = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(manager_id),
        json={"is_excluded": False, "reason": "other"},
    )

    assert response.status_code == 200
    assert response.json()["source"]["is_excluded"] is False
    assert response.json()["source"]["exclude_reason"] is None


def test_exclusion_revert_by_registrant_when_self_was_last_to_exclude_succeeds(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    member_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=member_id, email="m@x.test", role="member")
        sid = _insert_source(
            conn,
            project_id=pid,
            source_no=1,
            uploaded_by=member_id,
            is_excluded=True,
            exclude_reason="irrelevant",
        )
        conn.execute(
            "INSERT INTO source_audit_log (source_id, changed_by, field, old_value, "
            "new_value, reason) VALUES (%s, %s, 'is_excluded', 'false', 'true', 'irrelevant')",
            (sid, member_id),
        )
        conn.commit()

    response = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(member_id),
        json={"is_excluded": False, "reason": "other"},
    )

    assert response.status_code == 200
    assert response.json()["source"]["is_excluded"] is False


def test_exclusion_revert_by_registrant_when_manager_excluded_it_is_forbidden(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    """manager が除外した member のソースを本人が戻せないこと【C-4】。"""

    manager_id, member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, email="m@x.test", role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, email="u@x.test", role="member")
        sid = _insert_source(
            conn,
            project_id=pid,
            source_no=1,
            uploaded_by=member_id,
            is_excluded=True,
            exclude_reason="confidential",
        )
        # 最後の除外の操作は manager によるもの。
        conn.execute(
            "INSERT INTO source_audit_log (source_id, changed_by, field, old_value, "
            "new_value, reason) VALUES (%s, %s, 'is_excluded', 'false', 'true', 'confidential')",
            (sid, manager_id),
        )
        conn.commit()

    response = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(member_id),
        json={"is_excluded": False, "reason": "other"},
    )

    assert response.status_code == 403
    with psycopg.connect(migrated_database_url) as conn:
        is_excluded = conn.execute(
            "SELECT is_excluded FROM project_sources WHERE id = %s", (sid,)
        ).fetchone()[0]
    assert is_excluded is True


def test_exclusion_revert_by_other_member_forbidden(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    registrant_id, other_member_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=registrant_id, email="r@x.test", role="member")
        _insert_member(
            conn, project_id=pid, user_id=other_member_id, email="o@x.test", role="member"
        )
        sid = _insert_source(
            conn,
            project_id=pid,
            source_no=1,
            uploaded_by=registrant_id,
            is_excluded=True,
            exclude_reason="irrelevant",
        )
        conn.execute(
            "INSERT INTO source_audit_log (source_id, changed_by, field, old_value, "
            "new_value, reason) VALUES (%s, %s, 'is_excluded', 'false', 'true', 'irrelevant')",
            (sid, registrant_id),
        )
        conn.commit()

    response = api_client.patch(
        f"/internal/sources/{sid}/exclusion",
        headers=auth_headers(other_member_id),
        json={"is_excluded": False, "reason": "other"},
    )

    assert response.status_code == 403
