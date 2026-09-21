"""レポートの API（W18・W19、設計書 §2.4・§5.5・§5.6）。

W19 は版の種類（``audience``）もレポートの ID も入力に取らない【B-X3】。閲覧者の
ロールから worker が毎回選ぶ。footnotes・node_evidence の原文は、リクエストの
たびに ``source_segments.text`` から引く（I2）。
"""

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends

from ai_hackathon_team_a import authz, visibility
from ai_hackathon_team_a.api.deps import get_current_user, get_db_connection, require_worker_secret
from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.auth import AuthenticatedUser
from ai_hackathon_team_a.authz import Membership, Role

router = APIRouter(dependencies=[Depends(require_worker_secret)])

_WITHHELD_MESSAGE = (
    "この版には非公開になった情報が含まれるため表示できません。次の実行で作り直されます。"
)


@router.get("/internal/projects/{pid}/reports")
def list_reports(
    pid: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> list[dict]:
    """W18：所属者に、そのロールで見える版だけを一覧で返す。"""

    membership = _require_membership(conn, project_id=pid, user_id=user.user_id)

    if membership.role == "manager":
        rows = conn.execute(
            """
            SELECT DISTINCT ON (version_no)
                version_no, generated_at, audience, judge_status, input_segment_ids
            FROM reports
            WHERE project_id = %s
            ORDER BY version_no, (audience = 'managers') DESC
            """,
            (pid,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT version_no, generated_at, audience, judge_status, input_segment_ids
            FROM reports
            WHERE project_id = %s AND audience = 'all'
            ORDER BY version_no
            """,
            (pid,),
        ).fetchall()

    return [
        {
            "version_no": row[0],
            "generated_at": row[1],
            "audience": row[2],
            "judge_status": row[3],
            "withheld": _is_withheld(
                conn, project_id=pid, input_segment_ids=row[4], role=membership.role
            ),
        }
        for row in rows
    ]


@router.get("/internal/projects/{pid}/reports/{version_no}")
def get_report(
    pid: UUID,
    version_no: int,
    user: AuthenticatedUser = Depends(get_current_user),
    conn: psycopg.Connection = Depends(get_db_connection),
) -> dict:
    """W19：audience もレポートIDも入力に取らない。ロールから worker が選ぶ【B-X3】。"""

    membership = _require_membership(conn, project_id=pid, user_id=user.user_id)

    report = None
    if membership.role == "manager":
        report = authz.fetch_report_version_scoped(
            conn, project_id=pid, version_no=version_no, audience="managers"
        )
    if report is None:
        report = authz.fetch_report_version_scoped(
            conn, project_id=pid, version_no=version_no, audience="all"
        )
    if report is None:
        raise ApiError(404, "not_found", "レポートが見つかりません。")

    row = conn.execute(
        """
        SELECT judge_status, body_markdown, mermaid_dsl, evidence_catalog, event_ids,
               input_segment_ids, flags, summary_for_mail, generated_at, audience
        FROM reports WHERE id = %s
        """,
        (report.id,),
    ).fetchone()
    (
        judge_status,
        body_markdown,
        mermaid_dsl,
        evidence_catalog,
        event_ids,
        input_segment_ids,
        flags,
        summary_for_mail,
        generated_at,
        audience,
    ) = row

    withheld = _is_withheld(
        conn, project_id=pid, input_segment_ids=input_segment_ids, role=membership.role
    )

    excluded_summary = (
        _excluded_summary(conn, project_id=pid) if membership.role == "manager" else None
    )

    if withheld:
        return {
            "version_no": version_no,
            "audience": audience,
            "generated_at": generated_at,
            "judge_status": judge_status,
            "withheld": True,
            "body_markdown": _WITHHELD_MESSAGE,
            "footnotes": {},
            "mermaid_dsl": None,
            "node_evidence": {},
            "flags": {},
            "timeline": [],
            "excluded_summary": excluded_summary,
        }

    footnotes = {
        n: _segments_for_labels(conn, project_id=pid, labels=labels)
        for n, labels in (evidence_catalog or {}).items()
    }

    events_rows = conn.execute(
        """
        SELECT event_no, kind, summary, occurred_at, segment_ids
        FROM events
        WHERE project_id = %s AND event_no = ANY(%s)
        ORDER BY occurred_at
        """,
        (pid, event_ids or []),
    ).fetchall()

    node_evidence = {
        f"E{event_no}": _segments_for_labels(conn, project_id=pid, labels=segment_ids, minimal=True)
        for event_no, _, _, _, segment_ids in events_rows
    }
    timeline = [
        {"event_no": event_no, "occurred_at": occurred_at, "kind": kind, "summary": summary}
        for event_no, kind, summary, occurred_at, _ in events_rows
    ]

    return {
        "version_no": version_no,
        "audience": audience,
        "generated_at": generated_at,
        "judge_status": judge_status,
        "withheld": False,
        "body_markdown": body_markdown,
        "footnotes": footnotes,
        "mermaid_dsl": mermaid_dsl,
        "node_evidence": node_evidence,
        "flags": flags,
        "timeline": timeline,
        "excluded_summary": excluded_summary,
        "summary_for_mail": summary_for_mail,
    }


def _is_withheld(
    conn: psycopg.Connection, *, project_id: UUID, input_segment_ids: list[str], role: Role
) -> bool:
    """§5.5・§5.6：引用元ソースの今の状態を再確認し、見えないものがあれば版ごと隠す。"""

    states = visibility.fetch_source_states_for_labels(
        conn, project_id=project_id, labels=list(input_segment_ids or [])
    )
    if any(is_excluded for is_excluded, _ in states):
        return True
    if role == "member":
        return any(vis == "managers_only" for _, vis in states)
    return False


def _segments_for_labels(
    conn: psycopg.Connection, *, project_id: UUID, labels: list[str], minimal: bool = False
) -> list[dict]:
    if not labels:
        return []
    rows = conn.execute(
        """
        SELECT seg.label, ps.source_no, seg.speaker, ps.recorded_at, seg.text
        FROM source_segments seg
        JOIN source_versions sv ON sv.id = seg.source_version_id
        JOIN project_sources ps ON ps.id = sv.source_id
        WHERE seg.project_id = %s AND seg.label = ANY(%s)
        """,
        (project_id, list(dict.fromkeys(labels))),
    ).fetchall()
    if minimal:
        return [{"label": row[0], "text": row[4]} for row in rows]
    return [
        {
            "label": row[0],
            "source_no": row[1],
            "speaker": row[2],
            "recorded_at": row[3],
            "text": row[4],
        }
        for row in rows
    ]


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


def _require_membership(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID) -> Membership:
    membership = authz.get_membership(conn, project_id=project_id, user_id=user_id)
    if membership is None:
        raise ApiError(404, "not_found", "プロジェクトが見つかりません。")
    return membership
