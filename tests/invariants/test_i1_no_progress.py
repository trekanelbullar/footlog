"""I1（無進捗の判定）：設計書 §5.7、【B-X4】。

管理者限定の出来事（``partition = managers``）だけが新しく増えても、member 宛て
（実効の可視性が ``all`` の出来事だけで見る）の無進捗の判定は変わらない。
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import psycopg

from ai_hackathon_team_a import noprogress
from ai_hackathon_team_a.db import ConnectionPool


def _mock_mail_transport(captured: list[dict]) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"id": f"mock-{len(captured)}"})

    return httpx.MockTransport(_handler)


def _setup_project(database_url: str, *, threshold_hours: int) -> tuple[UUID, UUID, UUID]:
    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description, no_progress_threshold_hours) "
            "VALUES ('P', 'G', %s) RETURNING id",
            (threshold_hours,),
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        member_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members "
            "(project_id, user_id, email, role, notify_on_no_progress) VALUES "
            "(%s, %s, 'manager@example.test', 'manager', true), "
            "(%s, %s, 'member@example.test', 'member', true)",
            (project_id, manager_id, project_id, member_id),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'done') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    return project_id, run_id, manager_id


def _insert_event(
    database_url: str,
    *,
    project_id: UUID,
    run_id: UUID,
    event_no: int,
    partition: str,
    occurred_at: datetime,
) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO events (project_id, run_id, event_no, kind, summary, occurred_at, "
            "segment_ids, visibility_at_creation, partition) "
            "VALUES (%s, %s, %s, 'decision', 'ある決定', %s, '{}', %s, %s)",
            (
                project_id,
                run_id,
                event_no,
                occurred_at,
                "all" if partition == "all" else "managers_only",
                partition,
            ),
        )
        conn.commit()


def test_last_progress_at_for_member_ignores_managers_only_events(
    migrated_database_url: str,
) -> None:
    project_id, run_id, _manager_id = _setup_project(migrated_database_url, threshold_hours=1)
    now = datetime.now(UTC)
    old_all_time = now - timedelta(hours=10)
    recent_managers_time = now - timedelta(minutes=30)

    _insert_event(
        migrated_database_url,
        project_id=project_id,
        run_id=run_id,
        event_no=1,
        partition="all",
        occurred_at=old_all_time,
    )
    _insert_event(
        migrated_database_url,
        project_id=project_id,
        run_id=run_id,
        event_no=2,
        partition="managers",
        occurred_at=recent_managers_time,
    )

    with psycopg.connect(migrated_database_url) as conn:
        last_for_member = noprogress._last_progress_at(conn, project_id=project_id, audience="all")
        last_for_manager = noprogress._last_progress_at(
            conn, project_id=project_id, audience="managers"
        )

    # member（all）は managers-only の新しい出来事を見ないので、古い all の出来事のまま。
    assert last_for_member == old_all_time
    # manager は managers-only の出来事も見るので、より新しい時刻になる。
    assert last_for_manager == recent_managers_time
    assert last_for_member != last_for_manager


def test_managers_only_progress_does_not_suppress_member_no_progress_alert(
    migrated_database_url: str, worker_settings
) -> None:
    """B-X4：管理者限定の出来事だけが増えても、member 宛ての無進捗の判定は変わらない。"""

    project_id, run_id, _manager_id = _setup_project(migrated_database_url, threshold_hours=1)
    now = datetime.now(UTC)
    old_all_time = now - timedelta(hours=10)
    recent_managers_time = now - timedelta(minutes=30)

    _insert_event(
        migrated_database_url,
        project_id=project_id,
        run_id=run_id,
        event_no=1,
        partition="all",
        occurred_at=old_all_time,
    )
    _insert_event(
        migrated_database_url,
        project_id=project_id,
        run_id=run_id,
        event_no=2,
        partition="managers",
        occurred_at=recent_managers_time,
    )

    pool = ConnectionPool(migrated_database_url)
    captured: list[dict] = []
    transport = _mock_mail_transport(captured)

    with pool.connection() as conn:
        project = noprogress.fetch_project_for_no_progress(conn, project_id=project_id)
        sent = noprogress.check_and_notify_no_progress(
            conn,
            project=project,
            worker_settings=worker_settings,
            mail_transport=transport,
            now=now,
        )
        conn.commit()

    # managers（recipients_role='manager'）は閾値内（30分 < 1時間）で送らない。
    # all（recipients_role='member'）は閾値超過（10時間 >= 1時間）で送る。
    assert sent == 1
    assert len(captured) == 1

    with psycopg.connect(migrated_database_url) as conn:
        alerts = conn.execute(
            "SELECT audience, since FROM no_progress_alerts WHERE project_id = %s", (project_id,)
        ).fetchall()
        notifications = conn.execute(
            "SELECT kind FROM notifications WHERE project_id = %s AND kind = 'no_progress'",
            (project_id,),
        ).fetchall()
    assert len(alerts) == 1
    assert alerts[0][0] == "all"
    assert alerts[0][1] == old_all_time
    assert len(notifications) == 1
