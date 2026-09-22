"""実行の API（W14〜W16、設計書 §2.3・§5.1〜§5.3）。"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends

from ai_hackathon_team_a import authz, run
from ai_hackathon_team_a.api.deps import get_current_user, get_db_connection, require_worker_secret
from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.auth import AuthenticatedUser
from ai_hackathon_team_a.db import get_pool
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

router = APIRouter(dependencies=[Depends(require_worker_secret)])


@router.post("/internal/projects/{pid}/runs")
def create_run(
    pid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W14：所属者なら誰でも作れる（§1.3「手動実行」）。"""

    _require_membership(conn, project_id=pid, user_id=user.user_id)

    row = conn.execute(
        "INSERT INTO runs (project_id, trigger, status, created_by) "
        "VALUES (%s, 'manual', 'queued', %s) RETURNING id",
        (pid, user.user_id),
    ).fetchone()
    return {"run_id": row[0], "status": "queued"}


@router.post("/internal/runs/{rid}/execute")
def execute_run_endpoint(
    rid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
    settings: WorkerSettings = Depends(get_worker_settings),
) -> dict:
    """W15：完了まで待つ（設計書 §5.1）。リクエストの中で最後まで処理する。"""

    project_id = _project_id_for_run(conn, rid)
    _require_membership(conn, project_id=project_id, user_id=user.user_id)

    outcome = run.execute_run(rid, worker_settings=settings, pool=get_pool(settings))

    row = conn.execute(
        "SELECT status, step, outcome, error_message FROM runs WHERE id = %s", (rid,)
    ).fetchone()
    return {
        "run_id": rid,
        "status": row[0],
        "outcome": outcome.outcome,
        "version_no": outcome.version_no,
        "step": row[1],
        "error_message": row[3],
    }


@router.get("/internal/runs/{rid}")
def get_run(
    rid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W16：所属者なら誰でも見られる。"""

    project_id = _project_id_for_run(conn, rid)
    _require_membership(conn, project_id=project_id, user_id=user.user_id)

    row = conn.execute(
        "SELECT status, step, outcome, error_message FROM runs WHERE id = %s", (rid,)
    ).fetchone()
    version_no = conn.execute(
        "SELECT MAX(version_no) FROM reports WHERE run_id = %s", (rid,)
    ).fetchone()[0]

    return {
        "run_id": rid,
        "status": row[0],
        "step": row[1],
        "outcome": row[2],
        "version_no": version_no,
        "error_message": row[3],
    }


def _project_id_for_run(conn: psycopg.Connection, run_id: UUID) -> UUID:
    row = conn.execute("SELECT project_id FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "実行が見つかりません。")
    return row[0]


def _require_membership(
    conn: psycopg.Connection, *, project_id: UUID, user_id: UUID
) -> authz.Membership:
    membership = authz.get_membership(conn, project_id=project_id, user_id=user_id)
    if membership is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")
    return membership
