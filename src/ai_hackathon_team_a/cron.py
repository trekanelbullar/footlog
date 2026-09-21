"""定期実行（W17、設計書 §5.7）。

active なプロジェクトを1件ずつ、ロックが取れたら実行する（``trigger = schedule``）。
続けて、無進捗を宛先の立場ごとに判定する【B-X4】。全体が ``budget_seconds`` を
超えそうなら、残りのプロジェクトは次回に回す。
"""

import time
from dataclasses import dataclass
from uuid import UUID

import httpx

from ai_hackathon_team_a import noprogress
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import call_llm
from ai_hackathon_team_a.run import execute_run
from ai_hackathon_team_a.worker_settings import WorkerSettings

_DEFAULT_BUDGET_SECONDS = 240.0


@dataclass(frozen=True)
class CronTickResult:
    """W17 の応答（設計書 §2.3）。"""

    projects_checked: int
    runs_executed: int
    runs_skipped_locked: int
    no_progress_alerts_sent: int


def run_cron_tick(
    *,
    worker_settings: WorkerSettings,
    pool: ConnectionPool,
    llm_call=call_llm,
    mail_transport: httpx.BaseTransport | None = None,
    budget_seconds: float = _DEFAULT_BUDGET_SECONDS,
) -> CronTickResult:
    """W17 の実体。"""

    deadline = time.monotonic() + budget_seconds

    with pool.connection() as conn:
        project_ids: list[UUID] = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM projects WHERE status = 'active' ORDER BY created_at"
            ).fetchall()
        ]

    checked = 0
    executed = 0
    skipped_locked = 0
    for project_id in project_ids:
        if time.monotonic() > deadline:
            break
        checked += 1
        with pool.connection() as conn:
            run_id = conn.execute(
                "INSERT INTO runs (project_id, trigger, status) "
                "VALUES (%s, 'schedule', 'queued') RETURNING id",
                (project_id,),
            ).fetchone()[0]
            conn.commit()
        outcome = execute_run(
            run_id,
            worker_settings=worker_settings,
            pool=pool,
            llm_call=llm_call,
            mail_transport=mail_transport,
        )
        if outcome.outcome == "locked":
            skipped_locked += 1
        else:
            executed += 1

    alerts_sent = 0
    for project_id in project_ids:
        if time.monotonic() > deadline:
            break
        with pool.connection() as conn:
            project = noprogress.fetch_project_for_no_progress(conn, project_id=project_id)
            alerts_sent += noprogress.check_and_notify_no_progress(
                conn,
                project=project,
                worker_settings=worker_settings,
                mail_transport=mail_transport,
            )
            conn.commit()

    return CronTickResult(
        projects_checked=checked,
        runs_executed=executed,
        runs_skipped_locked=skipped_locked,
        no_progress_alerts_sent=alerts_sent,
    )
