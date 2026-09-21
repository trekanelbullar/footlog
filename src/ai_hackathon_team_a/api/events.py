"""出来事の検索（W25、追加指示 AD-12）。"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query

from ai_hackathon_team_a import visibility
from ai_hackathon_team_a.api.deps import get_current_user, get_db_connection, require_worker_secret
from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.auth import AuthenticatedUser
from ai_hackathon_team_a.authz import Membership, get_membership

router = APIRouter(dependencies=[Depends(require_worker_secret)])

_MIN_QUERY_CHARS = 2
_MAX_RESULTS = 50


@router.get("/internal/projects/{pid}/events/search")
def search_events(
    pid: UUID,
    q: str = Query(default=""),
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> list[dict]:
    """W25：出来事の要約・理由の部分一致検索（大小区別なし、2文字以上、最大50件、新しい順）。

    生の会話ログ（``source_segments`` の本文）は検索の対象にしない。閲覧者の
    ロールで見える出来事だけに絞る（member は実効の可視性が ``all`` のものだけ、
    manager は除外以外）。置き換えられた出来事も対象にする（過去の経緯を探すため）。
    """

    membership = _require_membership(conn, project_id=pid, user_id=user.user_id)

    query = q.strip()
    if len(query) < _MIN_QUERY_CHARS:
        raise ApiError(
            400, "query_too_short", f"検索文字列は{_MIN_QUERY_CHARS}文字以上で指定してください。"
        )

    rows = conn.execute(
        """
        SELECT event_no, kind, occurred_at, summary, reason, segment_ids, partition
        FROM events
        WHERE project_id = %s AND (summary ILIKE %s OR reason ILIKE %s)
        ORDER BY occurred_at DESC
        """,
        (pid, f"%{query}%", f"%{query}%"),
    ).fetchall()

    state_map = visibility.fetch_segment_states_for_project(conn, project_id=pid)

    results: list[dict] = []
    for event_no, kind, occurred_at, summary, reason, segment_ids, partition in rows:
        eff = visibility.effective_visibility(
            partition=partition,
            source_states=[state_map[sid] for sid in (segment_ids or []) if sid in state_map],
        )
        if eff == "excluded":
            continue
        if membership.role == "member" and eff != "all":
            continue

        results.append(
            {
                "event_no": event_no,
                "kind": kind,
                "occurred_at": occurred_at,
                "summary": summary,
                "reason": reason,
                "evidence": _evidence_for_labels(conn, project_id=pid, labels=segment_ids or []),
            }
        )
        if len(results) >= _MAX_RESULTS:
            break

    return results


def _evidence_for_labels(
    conn: psycopg.Connection, *, project_id: UUID, labels: list[str]
) -> list[dict]:
    """根拠の区切りの原文を、表示のたびに DB から引く（I2）。"""

    if not labels:
        return []
    unique_labels = list(dict.fromkeys(labels))
    rows = conn.execute(
        "SELECT label, text FROM source_segments WHERE project_id = %s AND label = ANY(%s)",
        (project_id, unique_labels),
    ).fetchall()
    text_by_label = dict(rows)
    return [
        {"label": label, "text": text_by_label[label]}
        for label in unique_labels
        if label in text_by_label
    ]


def _require_membership(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID) -> Membership:
    membership = get_membership(conn, project_id=project_id, user_id=user_id)
    if membership is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")
    return membership
