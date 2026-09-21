"""権限モデル（設計書 §1.3）を関数として提供する。

所属とロールはキャッシュせず、毎回 DB に問い合わせる。見つからない場合と権限が無い
場合は、どちらも呼び出し側で404として扱う（存在を知らせない）。
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import psycopg

Role = Literal["member", "manager"]
Audience = Literal["all", "managers"]


@dataclass(frozen=True)
class Membership:
    """あるプロジェクトにおける、あるユーザーの所属とロール。"""

    project_id: UUID
    user_id: UUID
    role: Role


def get_membership(
    conn: psycopg.Connection, *, project_id: UUID, user_id: UUID
) -> Membership | None:
    """(a) 所属とロールを DB で毎回引く。見つからなければ None（呼び出し側は404）。"""

    row = conn.execute(
        """
        SELECT project_id, user_id, role
        FROM project_members
        WHERE project_id = %s AND user_id = %s
        """,
        (project_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return Membership(project_id=row[0], user_id=row[1], role=row[2])


@dataclass(frozen=True)
class MemberRow:
    """W7・W7b（``/projects/{pid}/members/{uid}``）のように、親子を1条件で引いた結果。"""

    project_id: UUID
    user_id: UUID
    role: Role
    notify_on_progress: bool
    notify_on_no_progress: bool


def fetch_member_scoped(
    conn: psycopg.Connection, *, project_id: UUID, user_id: UUID
) -> MemberRow | None:
    """(b) 親と子を1つの条件で引く【A-X3】。

    子（``user_id``）だけで引いてから別に所属を確かめる書き方はしない。
    ``WHERE project_id = :pid AND user_id = :uid`` で一致しなければ None（404）。
    """

    row = conn.execute(
        """
        SELECT project_id, user_id, role, notify_on_progress, notify_on_no_progress
        FROM project_members
        WHERE project_id = %s AND user_id = %s
        """,
        (project_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return MemberRow(
        project_id=row[0],
        user_id=row[1],
        role=row[2],
        notify_on_progress=row[3],
        notify_on_no_progress=row[4],
    )


@dataclass(frozen=True)
class ReportVersionRow:
    """W19（``/projects/{pid}/reports/{version_no}``）のように、親子を1条件で引いた結果。"""

    id: UUID
    project_id: UUID
    version_no: int
    audience: Audience


def fetch_report_version_scoped(
    conn: psycopg.Connection, *, project_id: UUID, version_no: int, audience: Audience
) -> ReportVersionRow | None:
    """(b) の別例【A-X3】。

    ``audience`` は入力に取らず、呼び出し元（worker）が閲覧者のロールから決める
    【B-X3】。``WHERE project_id = :pid AND version_no = :v`` で一致しなければ None。
    """

    row = conn.execute(
        """
        SELECT id, project_id, version_no, audience
        FROM reports
        WHERE project_id = %s AND version_no = %s AND audience = %s
        """,
        (project_id, version_no, audience),
    ).fetchone()
    if row is None:
        return None
    return ReportVersionRow(id=row[0], project_id=row[1], version_no=row[2], audience=row[3])
