"""定期実行の無進捗判定（設計書 §5.7、【B-X4】）。

判定は宛先の立場ごとに分ける：manager 宛ては除外でない進捗系の出来事すべて、
member 宛ては実効の可視性が ``all`` の出来事だけを見る。しきい値は
``exclude_weekends`` なら土日を除いた経過時間で数える。``no_progress_alerts`` の
``(project_id, audience, since)`` の一意制約で、同じ「最後の進捗」に対する
再通知を防ぐ。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import psycopg

from ai_hackathon_team_a import notify, visibility
from ai_hackathon_team_a.worker_settings import WorkerSettings

_JST = ZoneInfo("Asia/Tokyo")

Audience = Literal["all", "managers"]

_PROGRESS_KINDS = ("decision", "rejected_option", "open_issue", "finding")


@dataclass(frozen=True)
class _Project:
    id: UUID
    created_at: datetime
    no_progress_threshold_hours: int
    exclude_weekends: bool


def _fetch_cost_limited_dates(conn: psycopg.Connection) -> set[date]:
    """AD-9：日次のコスト上限に達して分析を行わなかった日（日本時間）の一覧。

    ``daily_cost_alerts`` はプロジェクトをまたいだ全体で1日1行なので、全件を
    そのまま使う。
    """

    rows = conn.execute("SELECT alert_date FROM daily_cost_alerts").fetchall()
    return {row[0] for row in rows}


def _hours_excluding_days(
    start: datetime, end: datetime, *, exclude_weekends: bool, excluded_dates: set[date]
) -> float:
    """``start`` から ``end`` までの経過時間を、除外する日を飛ばして時間単位で数える。

    除外する日は、土日（``exclude_weekends`` のとき）と、日次のコスト上限に達して
    分析を行わなかった日（AD-9、``excluded_dates``。こちらは常に除外する）の
    どちらか。厳密な時分割ではなく、日単位で除外する近似（デモの規模で十分な精度）。
    """

    if end <= start:
        return 0.0

    total_hours = 0.0
    cursor = start
    one_day = timedelta(days=1)
    # その日の残り時間から、日をまたぎながら加算する。
    while cursor < end:
        day_end = min(
            end,
            datetime.combine(cursor.date(), datetime.min.time(), tzinfo=cursor.tzinfo) + one_day,
        )
        is_weekend = exclude_weekends and cursor.weekday() >= 5  # 5=土 6=日
        is_cost_limited_day = cursor.astimezone(_JST).date() in excluded_dates
        if not is_weekend and not is_cost_limited_day:
            total_hours += (day_end - cursor).total_seconds() / 3600
        cursor = day_end
    return total_hours


def _elapsed_hours(
    start: datetime, end: datetime, *, exclude_weekends: bool, excluded_dates: set[date]
) -> float:
    return _hours_excluding_days(
        start, end, exclude_weekends=exclude_weekends, excluded_dates=excluded_dates
    )


def _last_progress_at(
    conn: psycopg.Connection, *, project_id: UUID, audience: Audience
) -> datetime | None:
    """その立場から見える最後の進捗の時刻（【B-X4】）。"""

    rows = conn.execute(
        """
        SELECT occurred_at, segment_ids, partition
        FROM events
        WHERE project_id = %s AND kind = ANY(%s)
        """,
        (project_id, list(_PROGRESS_KINDS)),
    ).fetchall()

    states = visibility.fetch_segment_states_for_project(conn, project_id=project_id)

    latest: datetime | None = None
    for occurred_at, segment_ids, partition in rows:
        source_states = [states[sid] for sid in (segment_ids or []) if sid in states]
        eff = visibility.effective_visibility(partition=partition, source_states=source_states)
        if eff == "excluded":
            continue
        if audience == "all" and eff != "all":
            continue
        if latest is None or occurred_at > latest:
            latest = occurred_at
    return latest


def check_and_notify_no_progress(
    conn: psycopg.Connection,
    *,
    project: _Project,
    worker_settings: WorkerSettings,
    mail_transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> int:
    """1プロジェクト分の無進捗判定・通知（設計書 §5.7）。送った通知の件数を返す。"""

    now = now or datetime.now(UTC)
    cost_limited_dates = _fetch_cost_limited_dates(conn)
    sent = 0
    for audience, recipients_role in (("managers", "manager"), ("all", "member")):
        since = _last_progress_at(conn, project_id=project.id, audience=audience)
        baseline = since or project.created_at
        elapsed = _elapsed_hours(
            baseline,
            now,
            exclude_weekends=project.exclude_weekends,
            excluded_dates=cost_limited_dates,
        )
        if elapsed < project.no_progress_threshold_hours:
            continue

        already_sent = conn.execute(
            "SELECT 1 FROM no_progress_alerts "
            "WHERE project_id = %s AND audience = %s AND since = %s",
            (project.id, audience, baseline),
        ).fetchone()
        if already_sent is not None:
            continue

        recipients = conn.execute(
            "SELECT user_id, email FROM project_members "
            "WHERE project_id = %s AND role = %s AND notify_on_no_progress = true",
            (project.id, recipients_role),
        ).fetchall()
        if not recipients:
            continue

        conn.execute(
            "INSERT INTO no_progress_alerts (project_id, audience, since) VALUES (%s, %s, %s)",
            (project.id, audience, baseline),
        )
        last_progress_text = (
            f"最後に記録された進捗：{baseline.isoformat()}"
            if since
            else "まだ進捗が記録されていません。"
        )
        for user_id, email in recipients:
            notify.send_no_progress_notification(
                conn,
                project_id=project.id,
                user_id=user_id,
                email=email,
                last_progress_text=last_progress_text,
                settings=worker_settings,
                transport=mail_transport,
            )
        sent += 1
    return sent


def fetch_project_for_no_progress(conn: psycopg.Connection, *, project_id: UUID) -> _Project:
    row = conn.execute(
        "SELECT id, created_at, no_progress_threshold_hours, exclude_weekends "
        "FROM projects WHERE id = %s",
        (project_id,),
    ).fetchone()
    return _Project(
        id=row[0], created_at=row[1], no_progress_threshold_hours=row[2], exclude_weekends=row[3]
    )
