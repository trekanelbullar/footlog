"""AD-9：予算切れと無進捗の区別。

- ``daily_cost_alerts`` に行がある日は、無進捗の判定の経過時間から除く。
- ``cost_limited`` の実行で終わった日、manager にアプリ内通知が1日1回だけ。
- W3 の ``cost_limited_today``。
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg
from fastapi.testclient import TestClient

from ai_hackathon_team_a import noprogress
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import CostLimitExceeded
from ai_hackathon_team_a.notify import notify_cost_limited_once_per_day
from ai_hackathon_team_a.run import execute_run

AuthHeaders = Callable[..., dict[str, str]]
_JST = ZoneInfo("Asia/Tokyo")


def _mock_mail_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "mock-id"})

    return httpx.MockTransport(handler)


def _insert_project(
    conn: psycopg.Connection, *, threshold_hours: int = 24, created_at: datetime | None = None
) -> UUID:
    row = conn.execute(
        "INSERT INTO projects (name, goal_description, no_progress_threshold_hours) "
        "VALUES ('P', 'G', %s) RETURNING id",
        (threshold_hours,),
    ).fetchone()
    pid = row[0]
    if created_at is not None:
        conn.execute("UPDATE projects SET created_at = %s WHERE id = %s", (created_at, pid))
    conn.commit()
    return pid


def _insert_member(
    conn: psycopg.Connection, *, project_id: UUID, user_id: UUID, role: str = "member"
) -> None:
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, email, role) VALUES (%s, %s, %s, %s)",
        (project_id, user_id, f"{user_id}@example.test", role),
    )
    conn.commit()


def _insert_cost_alert(conn: psycopg.Connection, *, alert_date) -> None:
    conn.execute(
        "INSERT INTO daily_cost_alerts (alert_date) VALUES (%s) ON CONFLICT DO NOTHING",
        (alert_date,),
    )
    conn.commit()


# ---- 無進捗の判定から cost_limited の日を除く -------------------------------


def test_no_progress_alert_is_suppressed_when_the_gap_day_was_cost_limited(
    migrated_database_url: str, worker_settings
) -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    # しきい値24時間・プロジェクト作成は26時間前 → 除外が無ければ超過するはずの境界。
    created_at = now - timedelta(hours=26)

    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn, threshold_hours=24, created_at=created_at)
        member_id = uuid4()
        _insert_member(conn, project_id=pid, user_id=member_id, role="member")
        # まるまる1日、費用の上限に達していた（無進捗の経過時間から除かれるはずの日）。
        _insert_cost_alert(conn, alert_date=(now - timedelta(hours=13)).astimezone(_JST).date())
        conn.commit()

    with psycopg.connect(migrated_database_url) as conn:
        project = noprogress.fetch_project_for_no_progress(conn, project_id=pid)
        sent = noprogress.check_and_notify_no_progress(
            conn,
            project=project,
            worker_settings=worker_settings,
            mail_transport=_mock_mail_transport(),
            now=now,
        )
        conn.commit()
        alert_rows = conn.execute(
            "SELECT audience FROM no_progress_alerts WHERE project_id = %s", (pid,)
        ).fetchall()

    # 1日分が除かれるので、しきい値24時間を下回り、無進捗アラートは出ない。
    assert sent == 0
    assert alert_rows == []


def test_no_progress_alert_still_fires_without_the_cost_limited_exclusion(
    migrated_database_url: str, worker_settings
) -> None:
    """比較対照：費用の上限の記録が無ければ、同じ経過時間で無進捗アラートが出ること。"""

    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    created_at = now - timedelta(hours=26)

    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn, threshold_hours=24, created_at=created_at)
        member_id = uuid4()
        _insert_member(conn, project_id=pid, user_id=member_id, role="member")
        conn.commit()

    with psycopg.connect(migrated_database_url) as conn:
        project = noprogress.fetch_project_for_no_progress(conn, project_id=pid)
        sent = noprogress.check_and_notify_no_progress(
            conn,
            project=project,
            worker_settings=worker_settings,
            mail_transport=_mock_mail_transport(),
            now=now,
        )
        conn.commit()

    assert sent >= 1


# ---- cost_limited の実行 → manager への通知1日1回 ---------------------------


def test_notify_cost_limited_once_per_day_sends_to_managers_only_once(
    migrated_database_url: str,
) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        manager_id, member_id = uuid4(), uuid4()
        _insert_member(conn, project_id=pid, user_id=manager_id, role="manager")
        _insert_member(conn, project_id=pid, user_id=member_id, role="member")
        conn.commit()

    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    with psycopg.connect(migrated_database_url) as conn:
        notify_cost_limited_once_per_day(conn, project_id=pid, now=now)
        notify_cost_limited_once_per_day(conn, project_id=pid, now=now)  # 同じ日にもう一度
        conn.commit()
        rows = conn.execute(
            "SELECT user_id, kind, title FROM notifications WHERE project_id = %s", (pid,)
        ).fetchall()

    assert len(rows) == 1  # 1日1回だけ（2回呼んでも増えない）
    assert rows[0][0] == manager_id  # manager にだけ
    assert rows[0][1] == "cost_limited"
    assert "費用の上限" in rows[0][2]


def test_execute_run_cost_limited_notifies_manager_once(
    migrated_database_url: str, worker_settings
) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        manager_id = uuid4()
        _insert_member(conn, project_id=pid, user_id=manager_id, role="manager")
        # 抽出が呼ばれるよう、未消費の区切りを1件用意する（無ければ呼ばれず何も
        # 確かめられない）。
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (pid, manager_id),
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
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', 'Xを採用する', "
            "true, false)",
            (pid, version_id),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (pid,),
        ).fetchone()[0]
        conn.commit()

    pool = ConnectionPool(migrated_database_url)

    def _cost_limited_llm(*_a, **_k):
        raise CostLimitExceeded("budget exceeded")

    outcome = execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_cost_limited_llm
    )

    assert outcome.outcome == "cost_limited"
    with psycopg.connect(migrated_database_url) as conn:
        notif = conn.execute(
            "SELECT kind FROM notifications WHERE project_id = %s AND user_id = %s",
            (pid, manager_id),
        ).fetchone()
    assert notif == ("cost_limited",)


# ---- W3 の cost_limited_today ------------------------------------------------


def test_w3_cost_limited_today_reflects_todays_alert(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_id, role="member")

    before = api_client.get(f"/internal/projects/{pid}", headers=auth_headers(user_id))
    assert before.json()["cost_limited_today"] is False

    today_jst = datetime.now(_JST).date()
    with psycopg.connect(migrated_database_url) as conn:
        _insert_cost_alert(conn, alert_date=today_jst)

    after = api_client.get(f"/internal/projects/{pid}", headers=auth_headers(user_id))
    assert after.json()["cost_limited_today"] is True
