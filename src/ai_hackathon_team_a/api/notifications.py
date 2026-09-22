"""通知・質問の API（W20〜W23、設計書 §2.5・§5.4、【C-1】【C-2】）。"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ai_hackathon_team_a import ingest, run, visibility
from ai_hackathon_team_a.api.deps import get_current_user, get_db_connection, require_worker_secret
from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.auth import AuthenticatedUser
from ai_hackathon_team_a.authz import get_membership
from ai_hackathon_team_a.db import get_pool
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

router = APIRouter(dependencies=[Depends(require_worker_secret)])


class AnswerQuestionRequest(BaseModel):
    text: str = Field(min_length=1)


@router.get("/internal/me/notifications")
def list_my_notifications(
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> list[dict]:
    """W20：本人宛てのみ。"""

    rows = conn.execute(
        "SELECT id, kind, project_id, title, created_at, read_at, question_id "
        "FROM notifications WHERE user_id = %s ORDER BY created_at DESC",
        (user.user_id,),
    ).fetchall()
    return [
        {
            "id": row[0],
            "kind": row[1],
            "project_id": row[2],
            "title": row[3],
            "created_at": row[4],
            "read_at": row[5],
            "question_id": row[6],
        }
        for row in rows
    ]


@router.post("/internal/me/notifications/{nid}/read")
def mark_notification_read(
    nid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W21：本人宛てのみ（他人のものは404。親子を1条件で引く【A-X3】と同じ形）。"""

    updated = conn.execute(
        "UPDATE notifications SET read_at = now() WHERE id = %s AND user_id = %s RETURNING id",
        (nid, user.user_id),
    ).fetchone()
    if updated is None:
        raise ApiError(404, "not_found", "通知が見つかりません。")
    return {"ok": True}


@router.get("/internal/questions/{qid}")
def get_question(
    qid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W22：``asked_to`` 本人のみ。

    実効の可視性が今は見えなければ expired にして中身を返さない【C-1】。
    """

    row = conn.execute(
        "SELECT id, project_id, question, status, related_event_id, partition "
        "FROM agent_questions WHERE id = %s AND asked_to = %s",
        (qid, user.user_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "質問が見つかりません。")
    question_id, project_id, question_text, status, related_event_id, partition = row

    if status == "open" and not _still_visible(
        conn,
        project_id=project_id,
        user_id=user.user_id,
        related_event_id=related_event_id,
        partition=partition,
    ):
        conn.execute("UPDATE agent_questions SET status = 'expired' WHERE id = %s", (question_id,))
        status = "expired"

    if status == "expired":
        return {
            "question_id": question_id,
            "project_id": project_id,
            "question": "",
            "status": status,
            "related_event_summary": "",
        }

    event_row = conn.execute(
        "SELECT summary FROM events WHERE id = %s", (related_event_id,)
    ).fetchone()
    return {
        "question_id": question_id,
        "project_id": project_id,
        "question": question_text,
        "status": status,
        "related_event_summary": event_row[0] if event_row else "",
    }


def _still_visible(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    user_id: UUID,
    related_event_id: UUID,
    partition: str,
) -> bool:
    """質問を見せてよいか（【C-1】、宛先が今もその組を見られるか・実効の可視性）。"""

    if partition == "managers":
        membership = get_membership(conn, project_id=project_id, user_id=user_id)
        if membership is None or membership.role != "manager":
            return False

    event_row = conn.execute(
        "SELECT segment_ids FROM events WHERE id = %s", (related_event_id,)
    ).fetchone()
    segment_ids = list(event_row[0] or []) if event_row else []
    states = visibility.fetch_source_states_for_labels(
        conn, project_id=project_id, labels=segment_ids
    )
    eff = visibility.effective_visibility(partition=partition, source_states=states)
    if eff == "excluded":
        return False
    return not (partition == "all" and eff != "all")


@router.post("/internal/questions/{qid}/answer")
def answer_question(
    qid: UUID,
    payload: AnswerQuestionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
    settings: WorkerSettings = Depends(get_worker_settings),
) -> dict:
    """W23：``asked_to`` 本人のみ、``status = open`` のみ（設計書 §5.4、【C-2】）。

    可視性は回答した時点での元の出来事の実効の可視性から決める。取り込んだあと、
    同じプロジェクトの実行を1回作って走らせる（ロックが取れなければ次の定期実行に
    任せる）。
    """

    row = conn.execute(
        "SELECT project_id, related_event_id, partition, status "
        "FROM agent_questions WHERE id = %s AND asked_to = %s",
        (qid, user.user_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "質問が見つかりません。")
    project_id, related_event_id, partition, status = row
    if status != "open":
        raise ApiError(409, "question_not_open", "この質問は既に回答済みか期限切れです。")

    event_row = conn.execute(
        "SELECT segment_ids FROM events WHERE id = %s", (related_event_id,)
    ).fetchone()
    segment_ids = list(event_row[0] or []) if event_row else []
    states = visibility.fetch_source_states_for_labels(
        conn, project_id=project_id, labels=segment_ids
    )
    eff = visibility.effective_visibility(partition=partition, source_states=states)
    answer_visibility = "all" if eff == "all" else "managers_only"

    result = ingest.ingest_answer(
        conn,
        project_id=project_id,
        uploaded_by=user.user_id,
        text=payload.text,
        visibility=answer_visibility,
    )
    conn.execute(
        "UPDATE agent_questions SET status = 'answered', answer_source_id = %s WHERE id = %s",
        (result.source_id, qid),
    )
    new_run_id = conn.execute(
        "INSERT INTO runs (project_id, trigger, status, created_by) "
        "VALUES (%s, 'manual', 'queued', %s) RETURNING id",
        (project_id, user.user_id),
    ).fetchone()[0]
    conn.commit()

    run.execute_run(new_run_id, worker_settings=settings, pool=get_pool(settings))

    return {"source_id": result.source_id}
