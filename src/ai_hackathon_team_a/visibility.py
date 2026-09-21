"""実効の可視性（設計書 §5.5）。

出来事の「実効の可視性」は、保存してある ``visibility_at_creation`` とは別に、
根拠の区切りのソースの**今の**状態から、表示・利用のたびに計算する。この判定は
ここ（1関数）だけに置き、ほかで同じ判定を書かない。
"""

from typing import Literal
from uuid import UUID

import psycopg

from ai_hackathon_team_a.authz import Visibility

EffectiveVisibility = Literal["all", "managers_only", "excluded"]
Partition = Literal["all", "managers"]


def effective_visibility(
    *, partition: Partition, source_states: list[tuple[bool, Visibility]]
) -> EffectiveVisibility:
    """根拠の区切りのソースの状態から実効の可視性を計算する（§5.5）。

    ``source_states`` は根拠の区切りごとの ``(is_excluded, visibility)``。
    どれかが除外なら「除外」、どれかが ``managers_only`` なら ``managers_only``、
    それ以外は ``all``。ただし ``partition = managers`` の出来事は ``all`` には
    ならない（下限は ``managers_only``、【B-X1】）。
    """

    if any(is_excluded for is_excluded, _ in source_states):
        return "excluded"
    if partition == "managers":
        return "managers_only"
    if any(visibility == "managers_only" for _, visibility in source_states):
        return "managers_only"
    return "all"


def fetch_source_states_for_labels(
    conn: psycopg.Connection, *, project_id: UUID, labels: list[str]
) -> list[tuple[bool, Visibility]]:
    """区切りの番号（label）の一覧から、そのソースの今の ``(is_excluded, visibility)`` を引く。"""

    if not labels:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT ps.is_excluded, ps.visibility
        FROM source_segments seg
        JOIN source_versions sv ON sv.id = seg.source_version_id
        JOIN project_sources ps ON ps.id = sv.source_id
        WHERE seg.project_id = %s AND seg.label = ANY(%s)
        """,
        (project_id, labels),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]
