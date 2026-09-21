"""プロジェクト・メンバーの API（W1〜W7b、設計書 §2.1・§1.3）。"""

from typing import Literal
from uuid import UUID

import httpx
import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ai_hackathon_team_a import auth_admin, authz
from ai_hackathon_team_a.api.deps import (
    get_auth_admin_transport,
    get_current_user,
    get_db_connection,
    require_worker_secret,
)
from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.auth import AuthenticatedUser
from ai_hackathon_team_a.authz import Membership
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

router = APIRouter(dependencies=[Depends(require_worker_secret)])

_PROJECT_COLUMNS = (
    "id, name, goal_description, readme_markdown, status, "
    "no_progress_threshold_hours, exclude_weekends, created_at"
)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    goal_description: str = Field(min_length=1)
    readme_markdown: str | None = None
    no_progress_threshold_hours: int | None = Field(default=None, gt=0)
    exclude_weekends: bool | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    goal_description: str | None = None
    readme_markdown: str | None = None
    no_progress_threshold_hours: int | None = Field(default=None, gt=0)
    exclude_weekends: bool | None = None
    status: Literal["active", "completed"] | None = None


class AddMemberRequest(BaseModel):
    email: str = Field(min_length=1)
    role: Literal["member", "manager"]


class UpdateMemberRequest(BaseModel):
    role: Literal["member", "manager"] | None = None
    notify_on_progress: bool | None = None
    notify_on_no_progress: bool | None = None


@router.get("/internal/me/projects")
def list_my_projects(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> list[dict]:
    """W1：本人が所属するプロジェクトの一覧。"""

    rows = conn.execute(
        """
        SELECT p.id, p.name, pm.role, p.status,
               (SELECT MAX(r.version_no) FROM reports r WHERE r.project_id = p.id)
        FROM projects p
        JOIN project_members pm ON pm.project_id = p.id
        WHERE pm.user_id = %s
        ORDER BY p.created_at
        """,
        (user.user_id,),
    ).fetchall()
    return [
        {
            "project_id": row[0],
            "name": row[1],
            "role": row[2],
            "status": row[3],
            "latest_version_no": row[4],
        }
        for row in rows
    ]


@router.post("/internal/projects")
def create_project(
    payload: CreateProjectRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W2：ログイン済みなら誰でも作れる。作成者が最初の manager になる。"""

    if not user.email:
        raise ApiError(401, "unauthorized", "認証情報にメールアドレスが含まれていません。")

    row = conn.execute(
        """
        INSERT INTO projects (name, goal_description, readme_markdown,
                               no_progress_threshold_hours, exclude_weekends)
        VALUES (%s, %s, %s, COALESCE(%s, 24), COALESCE(%s, false))
        RETURNING id
        """,
        (
            payload.name,
            payload.goal_description,
            payload.readme_markdown,
            payload.no_progress_threshold_hours,
            payload.exclude_weekends,
        ),
    ).fetchone()
    project_id = row[0]
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, email, role) "
        "VALUES (%s, %s, %s, 'manager')",
        (project_id, user.user_id, user.email),
    )
    return {"project_id": project_id}


@router.get("/internal/projects/{pid}")
def get_project(
    pid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W3：所属者なら誰でも見られる。除外の件数は manager にだけ出す。"""

    membership = _require_membership(conn, project_id=pid, user_id=user.user_id)
    row = conn.execute(f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE id = %s", (pid,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")

    latest_run_row = conn.execute(
        "SELECT id, status, step, outcome, started_at, finished_at FROM runs "
        "WHERE project_id = %s ORDER BY started_at DESC NULLS LAST LIMIT 1",
        (pid,),
    ).fetchone()
    latest_run = None
    if latest_run_row is not None:
        latest_run = {
            "run_id": latest_run_row[0],
            "status": latest_run_row[1],
            "step": latest_run_row[2],
            "outcome": latest_run_row[3],
            "started_at": latest_run_row[4],
            "finished_at": latest_run_row[5],
        }

    excluded_summary = None
    if membership.role == "manager":
        excluded_summary = _excluded_summary(conn, project_id=pid)

    return {
        "project": _project_dict(row),
        "my_role": membership.role,
        "latest_run": latest_run,
        "excluded_summary": excluded_summary,
    }


@router.patch("/internal/projects/{pid}")
def update_project(
    pid: UUID,
    payload: UpdateProjectRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W4：manager だけが変更できる。"""

    membership = _require_membership(conn, project_id=pid, user_id=user.user_id)
    _require_manager(membership)

    updates = payload.model_dump(exclude_unset=True)
    if updates:
        set_clause = ", ".join(f"{field} = %s" for field in updates)
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id = %s", (*updates.values(), pid))

    row = conn.execute(f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE id = %s", (pid,)).fetchone()
    return {"project": _project_dict(row)}


@router.get("/internal/projects/{pid}/members")
def list_members(
    pid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> list[dict]:
    """W5：所属者なら誰でも見られる。"""

    _require_membership(conn, project_id=pid, user_id=user.user_id)
    rows = conn.execute(
        "SELECT user_id, email, role, notify_on_progress, notify_on_no_progress "
        "FROM project_members WHERE project_id = %s ORDER BY email",
        (pid,),
    ).fetchall()
    return [_member_row_dict(row) for row in rows]


@router.post("/internal/projects/{pid}/members")
def add_member(
    pid: UUID,
    payload: AddMemberRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
    settings: WorkerSettings = Depends(get_worker_settings),
    transport: httpx.BaseTransport | None = Depends(get_auth_admin_transport),
) -> dict:
    """W6：manager だけが追加できる。未登録のメールアドレスなら404。"""

    membership = _require_membership(conn, project_id=pid, user_id=user.user_id)
    _require_manager(membership)

    found_user_id = auth_admin.find_user_id_by_email(
        payload.email, settings=settings, transport=transport
    )
    if found_user_id is None:
        raise ApiError(404, "user_not_found", "先にサインアップしてもらってください。")

    already_member = conn.execute(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s",
        (pid, found_user_id),
    ).fetchone()
    if already_member is not None:
        raise ApiError(409, "already_member", "既にメンバーです。")

    conn.execute(
        "INSERT INTO project_members (project_id, user_id, email, role) VALUES (%s, %s, %s, %s)",
        (pid, found_user_id, payload.email, payload.role),
    )
    _bump_visibility_epoch(conn, pid)
    return {"user_id": found_user_id, "role": payload.role}


@router.patch("/internal/projects/{pid}/members/{uid}")
def update_member(
    pid: UUID,
    uid: UUID,
    payload: UpdateMemberRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W7：role は manager だけ、通知設定は本人か manager（親子を1条件で引く、A-X3）。"""

    caller = _require_membership(conn, project_id=pid, user_id=user.user_id)
    target = authz.fetch_member_scoped(conn, project_id=pid, user_id=uid)
    if target is None:
        raise ApiError(404, "not_found", "メンバーが見つかりません。")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return {"member": _member_dict(conn, project_id=pid, user_id=uid)}

    if "role" in updates and caller.role != "manager":
        raise ApiError(403, "forbidden", "manager だけがロールを変更できます。")

    notify_fields = {"notify_on_progress", "notify_on_no_progress"} & updates.keys()
    if notify_fields and caller.role != "manager" and user.user_id != uid:
        raise ApiError(403, "forbidden", "本人か manager だけが通知設定を変更できます。")

    role_changing = "role" in updates and updates["role"] != target.role
    if role_changing and target.role == "manager" and updates["role"] != "manager":
        _ensure_not_last_manager(conn, project_id=pid, user_id=uid)

    set_clause = ", ".join(f"{field} = %s" for field in updates)
    conn.execute(
        f"UPDATE project_members SET {set_clause} WHERE project_id = %s AND user_id = %s",
        (*updates.values(), pid, uid),
    )

    if role_changing:
        _bump_visibility_epoch(conn, pid)

    return {"member": _member_dict(conn, project_id=pid, user_id=uid)}


@router.delete("/internal/projects/{pid}/members/{uid}")
def delete_member(
    pid: UUID,
    uid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W7b：manager だけ。最後の manager は削除できない（409 last_manager、A-X1）。"""

    caller = _require_membership(conn, project_id=pid, user_id=user.user_id)
    _require_manager(caller)

    target = authz.fetch_member_scoped(conn, project_id=pid, user_id=uid)
    if target is None:
        raise ApiError(404, "not_found", "メンバーが見つかりません。")

    if target.role == "manager":
        _ensure_not_last_manager(conn, project_id=pid, user_id=uid)

    conn.execute("DELETE FROM project_members WHERE project_id = %s AND user_id = %s", (pid, uid))
    _bump_visibility_epoch(conn, pid)
    return {"ok": True}


def _require_membership(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID) -> Membership:
    membership = authz.get_membership(conn, project_id=project_id, user_id=user_id)
    if membership is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")
    return membership


def _require_manager(membership: Membership) -> None:
    if membership.role != "manager":
        raise ApiError(403, "forbidden", "manager だけが行えます。")


def _ensure_not_last_manager(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID) -> None:
    manager_ids = [
        row[0]
        for row in conn.execute(
            "SELECT user_id FROM project_members "
            "WHERE project_id = %s AND role = 'manager' FOR UPDATE",
            (project_id,),
        ).fetchall()
    ]
    if manager_ids == [user_id]:
        raise ApiError(409, "last_manager", "最後の manager を降格・削除することはできません。")


def _bump_visibility_epoch(conn: psycopg.Connection, project_id: UUID) -> None:
    """ロール・所属の変更で visibility_epoch を進め、次の実行での作り直しを予約する（§5.5）。"""

    conn.execute(
        "UPDATE projects SET visibility_epoch = visibility_epoch + 1, needs_rebuild = true "
        "WHERE id = %s",
        (project_id,),
    )


def _excluded_summary(conn: psycopg.Connection, *, project_id: UUID) -> dict:
    rows = conn.execute(
        "SELECT exclude_reason, COUNT(*) FROM project_sources "
        "WHERE project_id = %s AND is_excluded = true GROUP BY exclude_reason",
        (project_id,),
    ).fetchall()
    by_reason = {"private": 0, "confidential": 0, "irrelevant": 0, "other": 0}
    total = 0
    for reason, count in rows:
        by_reason[reason] = count
        total += count
    return {"count": total, "by_reason": by_reason}


def _project_dict(row: tuple) -> dict:
    return {
        "id": row[0],
        "name": row[1],
        "goal_description": row[2],
        "readme_markdown": row[3],
        "status": row[4],
        "no_progress_threshold_hours": row[5],
        "exclude_weekends": row[6],
        "created_at": row[7],
    }


def _member_row_dict(row: tuple) -> dict:
    return {
        "user_id": row[0],
        "email": row[1],
        "role": row[2],
        "notify_on_progress": row[3],
        "notify_on_no_progress": row[4],
    }


def _member_dict(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID) -> dict:
    row = conn.execute(
        "SELECT user_id, email, role, notify_on_progress, notify_on_no_progress "
        "FROM project_members WHERE project_id = %s AND user_id = %s",
        (project_id, user_id),
    ).fetchone()
    return _member_row_dict(row)
