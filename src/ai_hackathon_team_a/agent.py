"""エージェントのループ・質問（設計書 §5.3 (7)(8)(10)(12)、【C-1】）。

出来事1件ごとに、道具（tool）を最大5回呼べるループを回す。道具が返す内容も
資料（データ）として扱い、その中の指示には従わない前提は ``prompts/agent.txt``
に書いてある。ここ（コード）で行うのは、道具の実行そのものと、質問の宛先の
決定・送信直前の再確認【C-1】、置き換えの出来事の番号・裏付けの検査。
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import psycopg

from ai_hackathon_team_a import authz, notify, visibility
from ai_hackathon_team_a.authz import Visibility as SourceVisibility
from ai_hackathon_team_a.pipeline.contracts import (
    ExtractedEvent,
    SegmentIn,
    SupportInput,
    SupportItem,
)
from ai_hackathon_team_a.pipeline.extract import correct_origin
from ai_hackathon_team_a.pipeline.support import check_support
from ai_hackathon_team_a.prompts import get_prompts, render
from ai_hackathon_team_a.redact import redact
from ai_hackathon_team_a.worker_settings import WorkerSettings

logger = logging.getLogger(__name__)

Partition = Literal["all", "managers"]

_MAX_TOOL_ROUNDS_PER_EVENT = 5
_MAX_QUESTIONS_PER_RUN = 2
_MAX_READ_SEGMENTS = 10
_MAX_SEARCH_RESULTS = 10
# AD-11：1人あたり、日本時間の1日に届く質問は最大3問まで（全プロジェクト合計）。
_MAX_QUESTIONS_PER_DAY = 3
_JST = ZoneInfo("Asia/Tokyo")

_TOOLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "read_segments",
            "description": (
                "この出来事の根拠に含まれるソースの区切りの前後を読みます（前後それぞれ最大10件）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "segment_id": {
                        "type": "string",
                        "description": "基準にする区切りの番号（例：S3-12）。根拠にあるものだけ。",
                    },
                    "before": {"type": "integer", "description": "前に読む件数（最大10）"},
                    "after": {"type": "integer", "description": "後に読む件数（最大10）"},
                },
                "required": ["segment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": (
                "同じ公開範囲の、今も有効な出来事を要約の部分一致で探します（最大10件）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

_ASK_MEMBER_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "ask_member",
        "description": (
            "担当者に質問します。1回の実行で使える回数に上限があり、この出来事へは"
            "1回だけ使えます。本当に必要なときだけ使ってください。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
}


@dataclass(frozen=True)
class AgentCandidate:
    """エージェントのループの起動対象になる1件の出来事（設計書 §5.3 (7)）。"""

    event_id: UUID
    event_no: int
    kind: str
    summary: str
    reason: str | None
    occurred_at: datetime
    segment_ids: list[str]
    partition: Partition


@dataclass(frozen=True)
class ResolvedEvent:
    """理由が判明し、置き換えの新しい出来事を追記した結果。"""

    candidate: AgentCandidate
    event_no: int


def needs_agent(
    *,
    kind: str,
    reason: str | None,
    supersedes_event_no: int | None,
    conflicts_with_event_no: int | None,
) -> bool:
    """起動条件（設計書 §5.3 (7)）：抽出直後の情報だけで判定できる形。"""

    if kind in ("decision", "rejected_option") and not reason:
        return True
    return supersedes_event_no is None and conflicts_with_event_no is not None


def run_agent_for_candidates(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    candidates: list[AgentCandidate],
    llm_fn,
    goal_description: str,
    start_epoch: int,
    worker_settings: WorkerSettings,
    allocate_event_no,
    mail_transport: httpx.BaseTransport | None = None,
) -> list[ResolvedEvent]:
    """出来事ごとにループを回し、理由が判明したものだけ置き換えの出来事を追記する。

    ``allocate_event_no`` は ``run.py`` の連番払い出し関数（循環 import を避けるため
    呼び出し側から渡す）。
    """

    resolved: list[ResolvedEvent] = []
    questions_asked = 0

    for candidate in candidates:
        recipient = _find_recipient(conn, project_id=project_id, candidate=candidate)
        allow_ask = recipient is not None and questions_asked < _MAX_QUESTIONS_PER_RUN

        outcome, asked = _run_single_event_loop(
            conn,
            project_id=project_id,
            run_id=run_id,
            candidate=candidate,
            llm_fn=llm_fn,
            goal_description=goal_description,
            start_epoch=start_epoch,
            worker_settings=worker_settings,
            recipient=recipient if allow_ask else None,
            mail_transport=mail_transport,
        )
        if asked:
            questions_asked += 1

        if outcome is not None:
            new_event_no = _append_resolution(
                conn,
                project_id=project_id,
                run_id=run_id,
                candidate=candidate,
                resolution=outcome,
                allocate_event_no=allocate_event_no,
                llm_fn=llm_fn,
            )
            resolved.append(ResolvedEvent(candidate=candidate, event_no=new_event_no))

    return resolved


# ---------------------------------------------------------------------------
# 質問の宛先の決定【C-1】
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Recipient:
    user_id: UUID
    email: str


def _find_recipient(
    conn: psycopg.Connection, *, project_id: UUID, candidate: AgentCandidate
) -> _Recipient | None:
    """【C-1】：all の組は根拠の最初の区切りのソースの登録者、managers の組は
    根拠のソースの登録者のうち manager のロールを持つ人（該当者がいなければ None）。
    """

    if not candidate.segment_ids:
        return None

    if candidate.partition == "all":
        uploader = _uploader_of_segment(conn, candidate.segment_ids[0])
        if uploader is None:
            return None
        return _as_recipient(conn, project_id=project_id, user_id=uploader)

    uploaders = _uploaders_of_segments(conn, candidate.segment_ids)
    for user_id in uploaders:
        membership = authz.get_membership(conn, project_id=project_id, user_id=user_id)
        if membership is not None and membership.role == "manager":
            return _as_recipient(conn, project_id=project_id, user_id=user_id)
    return None


def _as_recipient(
    conn: psycopg.Connection, *, project_id: UUID, user_id: UUID
) -> _Recipient | None:
    row = conn.execute(
        "SELECT email FROM project_members WHERE project_id = %s AND user_id = %s",
        (project_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return _Recipient(user_id=user_id, email=row[0])


def _uploader_of_segment(conn: psycopg.Connection, label: str) -> UUID | None:
    row = conn.execute(
        """
        SELECT ps.uploaded_by
        FROM source_segments seg
        JOIN source_versions sv ON sv.id = seg.source_version_id
        JOIN project_sources ps ON ps.id = sv.source_id
        WHERE seg.label = %s
        """,
        (label,),
    ).fetchone()
    return row[0] if row else None


def _uploaders_of_segments(conn: psycopg.Connection, labels: list[str]) -> list[UUID]:
    if not labels:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT ps.uploaded_by
        FROM source_segments seg
        JOIN source_versions sv ON sv.id = seg.source_version_id
        JOIN project_sources ps ON ps.id = sv.source_id
        WHERE seg.label = ANY(%s)
        """,
        (labels,),
    ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# 1件の出来事のループ
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Resolution:
    summary: str
    reason: str
    segment_ids: list[str]


def _run_single_event_loop(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    candidate: AgentCandidate,
    llm_fn,
    goal_description: str,
    start_epoch: int,
    worker_settings: WorkerSettings,
    recipient: _Recipient | None,
    mail_transport: httpx.BaseTransport | None,
) -> tuple[_Resolution | None, bool]:
    seen_segment_ids: set[str] = set(candidate.segment_ids)
    asked_already = False
    asked_this_event = False

    messages: list[dict[str, object]] = _build_initial_messages(
        conn, candidate=candidate, goal_description=goal_description
    )

    for _round in range(_MAX_TOOL_ROUNDS_PER_EVENT):
        tools = list(_TOOLS)
        if recipient is not None and not asked_this_event:
            tools.append(_ASK_MEMBER_TOOL)

        result = llm_fn("AGENT", messages, json_mode=False, tools=tools)
        tool_calls = getattr(result, "tool_calls", None)
        text = getattr(result, "text", None)

        if not tool_calls:
            return _parse_final_answer(text, seen_segment_ids), asked_already

        messages.append({"role": "assistant", "content": text or "", "tool_calls": tool_calls})

        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}

            if name == "read_segments":
                tool_result = _tool_read_segments(
                    conn,
                    candidate=candidate,
                    arguments=arguments,
                    seen_segment_ids=seen_segment_ids,
                )
            elif name == "search_events":
                tool_result = _tool_search_events(conn, candidate=candidate, arguments=arguments)
            elif name == "ask_member" and recipient is not None and not asked_this_event:
                asked_this_event = True
                sent = _tool_ask_member(
                    conn,
                    project_id=project_id,
                    run_id=run_id,
                    candidate=candidate,
                    recipient=recipient,
                    question=str(arguments.get("question") or ""),
                    start_epoch=start_epoch,
                    worker_settings=worker_settings,
                    mail_transport=mail_transport,
                )
                if sent:
                    asked_already = True
                tool_result = {"sent": sent}
            else:
                tool_result = {"error": "この道具は今は使えません。"}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )

    # ループの上限に達した。最後にもう一度、道具なしで結論だけ聞く。
    final = llm_fn("AGENT", messages, json_mode=False, tools=None)
    return _parse_final_answer(getattr(final, "text", None), seen_segment_ids), asked_already


def _build_initial_messages(
    conn: psycopg.Connection, *, candidate: AgentCandidate, goal_description: str
) -> list[dict[str, object]]:
    system = render(get_prompts()["agent"], goal_description=goal_description)
    segments = _segments_by_labels(conn, candidate.segment_ids)
    evidence = "\n".join(f"[{s.label}] {s.text}" for s in segments)
    user = (
        f"# 出来事\n種類：{candidate.kind}\n要約：{candidate.summary}\n"
        f"現在の理由：{candidate.reason or '（空欄）'}\n\n# 根拠の原文\n{evidence}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_final_answer(text: str | None, seen_segment_ids: set[str]) -> _Resolution | None:
    if not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("resolved"):
        return None

    reason = data.get("reason")
    summary = data.get("summary")
    raw_segment_ids = data.get("segment_ids")
    if not isinstance(reason, str) or not reason.strip():
        return None
    if not isinstance(raw_segment_ids, list):
        return None
    kept_ids = [sid for sid in raw_segment_ids if isinstance(sid, str) and sid in seen_segment_ids]
    if not kept_ids:
        return None

    clean_reason, _ = redact(reason.strip())
    clean_summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
    if clean_summary is not None:
        clean_summary, _ = redact(clean_summary)
    return _Resolution(summary=clean_summary or "", reason=clean_reason, segment_ids=kept_ids)


# ---------------------------------------------------------------------------
# 道具の実装
# ---------------------------------------------------------------------------


def _tool_read_segments(
    conn: psycopg.Connection,
    *,
    candidate: AgentCandidate,
    arguments: dict,
    seen_segment_ids: set[str],
) -> dict:
    segment_id = arguments.get("segment_id")
    if not isinstance(segment_id, str) or segment_id not in candidate.segment_ids:
        return {"error": "この出来事の根拠に含まれる区切りだけ指定できます。"}

    before = _clamp_int(arguments.get("before"), default=0)
    after = _clamp_int(arguments.get("after"), default=0)

    row = conn.execute(
        "SELECT project_id, source_version_id, seq FROM source_segments WHERE label = %s",
        (segment_id,),
    ).fetchone()
    if row is None:
        return {"error": "区切りが見つかりません。"}
    project_id, source_version_id, seq = row

    rows = conn.execute(
        """
        SELECT label, seq, speaker, text FROM source_segments
        WHERE source_version_id = %s AND seq BETWEEN %s AND %s
        ORDER BY seq
        """,
        (source_version_id, seq - before, seq + after),
    ).fetchall()

    for r in rows:
        seen_segment_ids.add(r[0])
    return {"segments": [{"label": r[0], "speaker": r[2], "text": r[3]} for r in rows]}


def _tool_search_events(
    conn: psycopg.Connection, *, candidate: AgentCandidate, arguments: dict
) -> dict:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return {"events": []}

    project_id = _project_id_of(conn, candidate.event_id)
    rows = conn.execute(
        """
        SELECT id, event_no, summary, kind, segment_ids, partition, supersedes_event_id
        FROM events WHERE project_id = %s
        """,
        (project_id,),
    ).fetchall()

    state_map = visibility.fetch_segment_states_for_project(conn, project_id=project_id)
    eligible: dict[UUID, tuple] = {}
    for row in rows:
        event_id, event_no, summary, kind, segment_ids, partition, supersedes_event_id = row
        eff = visibility.effective_visibility(
            partition=partition,
            source_states=[state_map[sid] for sid in (segment_ids or []) if sid in state_map],
        )
        if eff == "excluded":
            continue
        if candidate.partition == "all" and eff != "all":
            continue
        eligible[event_id] = (event_no, summary, kind, supersedes_event_id)

    superseded = {v[3] for v in eligible.values() if v[3] is not None and v[3] in eligible}
    matches = [
        {"event_no": no, "summary": summary}
        for eid, (no, summary, kind, _) in eligible.items()
        # AD-10：置き換えられた出来事は検索対象から外すが、過去の rejected_option
        # （再浮上の検知の対象）だけは、置き換えられていても検索できるようにする。
        if (eid not in superseded or kind == "rejected_option")
        and query.lower() in summary.lower()
        and eid != candidate.event_id
    ]
    return {"events": matches[:_MAX_SEARCH_RESULTS]}


def _tool_ask_member(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    candidate: AgentCandidate,
    recipient: _Recipient,
    question: str,
    start_epoch: int,
    worker_settings: WorkerSettings,
    mail_transport: httpx.BaseTransport | None,
) -> bool:
    """質問を保存する。

    送る直前の再確認【C-1】で条件を満たさなければ ``expired`` で保存し、送らない。
    条件は満たすが、その人のその日の質問の枠（AD-11、最大3問／日、全プロジェクト
    合計）が埋まっていれば ``deferred`` で保存し、メールもアプリ内通知も送らない
    （定期実行の最初に ``send_deferred_questions`` が古い順に送る）。
    """

    clean_question, _ = redact(question.strip() or "（質問内容が空でした）")

    can_send = _can_send_question_now(
        conn,
        project_id=project_id,
        candidate=candidate,
        recipient=recipient,
        start_epoch=start_epoch,
    )
    if not can_send:
        status = "expired"
    elif _todays_question_count(conn, asked_to=recipient.user_id) >= _MAX_QUESTIONS_PER_DAY:
        status = "deferred"
    else:
        status = "open"

    row = conn.execute(
        """
        INSERT INTO agent_questions
            (project_id, run_id, asked_to, question, related_event_id, partition, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            project_id,
            run_id,
            recipient.user_id,
            clean_question,
            candidate.event_id,
            candidate.partition,
            status,
        ),
    ).fetchone()
    question_id = row[0]

    if status != "open":
        return False

    notify.send_question_notification(
        conn,
        project_id=project_id,
        user_id=recipient.user_id,
        email=recipient.email,
        question_id=question_id,
        question_text=clean_question,
        settings=worker_settings,
        transport=mail_transport,
    )
    return True


def _start_of_jst_day(now: datetime) -> datetime:
    jst_now = now.astimezone(_JST)
    return jst_now.replace(hour=0, minute=0, second=0, microsecond=0)


def _todays_question_count(
    conn: psycopg.Connection, *, asked_to: UUID, now: datetime | None = None
) -> int:
    """AD-11：その人に、日本時間の今日すでに届いた（＝ deferred でない）質問の数。

    全プロジェクト合計。厳密には「作成時点で expired になったもの」もここに含まれ
    うる（後から状態だけ見ると作成時に送られたか区別できないため）が、通常の
    利用では稀な近似として許容する。
    """

    start_of_day = _start_of_jst_day(now or datetime.now(UTC))
    row = conn.execute(
        "SELECT COUNT(*) FROM agent_questions "
        "WHERE asked_to = %s AND status != 'deferred' AND created_at >= %s",
        (asked_to, start_of_day),
    ).fetchone()
    return int(row[0]) if row else 0


def _recipient_still_eligible(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    partition: Partition,
    segment_ids: list[str],
    asked_to: UUID,
) -> bool:
    """【C-1】の visibility・role の再確認（epoch は含まない部分）。"""

    if partition == "managers":
        membership = authz.get_membership(conn, project_id=project_id, user_id=asked_to)
        if membership is None or membership.role != "manager":
            return False

    states = visibility.fetch_source_states_for_labels(
        conn, project_id=project_id, labels=segment_ids
    )
    eff = visibility.effective_visibility(partition=partition, source_states=states)
    if eff == "excluded":
        return False
    return not (partition == "all" and eff != "all")


def send_deferred_questions(
    conn: psycopg.Connection,
    *,
    worker_settings: WorkerSettings,
    mail_transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> int:
    """AD-11：定期実行（W17）の最初に、枠が空いている ``deferred`` の質問を

    古い順に送り、``open`` にする。送る直前の可視性・ロールの再確認【C-1】は
    ``ask_member`` と同じ。枠が今日もう無い人はそのまま ``deferred`` に残す。
    再確認で条件を満たさなくなっていれば ``expired`` にする（枠を消費しない）。
    送った件数を返す。
    """

    now = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT id, project_id, asked_to, question, related_event_id, partition "
        "FROM agent_questions WHERE status = 'deferred' ORDER BY created_at ASC"
    ).fetchall()

    remaining_by_user: dict[UUID, int] = {}
    sent = 0

    for question_id, project_id, asked_to, question_text, related_event_id, partition in rows:
        event_row = conn.execute(
            "SELECT segment_ids FROM events WHERE id = %s", (related_event_id,)
        ).fetchone()
        segment_ids = list(event_row[0] or []) if event_row else []

        if not _recipient_still_eligible(
            conn,
            project_id=project_id,
            partition=partition,
            segment_ids=segment_ids,
            asked_to=asked_to,
        ):
            conn.execute(
                "UPDATE agent_questions SET status = 'expired' WHERE id = %s", (question_id,)
            )
            continue

        if asked_to not in remaining_by_user:
            remaining_by_user[asked_to] = _MAX_QUESTIONS_PER_DAY - _todays_question_count(
                conn, asked_to=asked_to, now=now
            )
        if remaining_by_user[asked_to] <= 0:
            continue  # 今日の枠が無い。deferred のまま次回に回す。

        recipient = _as_recipient(conn, project_id=project_id, user_id=asked_to)
        if recipient is None:
            conn.execute(
                "UPDATE agent_questions SET status = 'expired' WHERE id = %s", (question_id,)
            )
            continue

        conn.execute("UPDATE agent_questions SET status = 'open' WHERE id = %s", (question_id,))
        notify.send_question_notification(
            conn,
            project_id=project_id,
            user_id=asked_to,
            email=recipient.email,
            question_id=question_id,
            question_text=question_text,
            settings=worker_settings,
            transport=mail_transport,
        )
        remaining_by_user[asked_to] -= 1
        sent += 1

    return sent


def _can_send_question_now(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    candidate: AgentCandidate,
    recipient: _Recipient,
    start_epoch: int,
) -> bool:
    epoch_row = conn.execute(
        "SELECT visibility_epoch FROM projects WHERE id = %s", (project_id,)
    ).fetchone()
    if epoch_row is None or epoch_row[0] != start_epoch:
        return False

    return _recipient_still_eligible(
        conn,
        project_id=project_id,
        partition=candidate.partition,
        segment_ids=candidate.segment_ids,
        asked_to=recipient.user_id,
    )


# ---------------------------------------------------------------------------
# 置き換えの出来事の追記
# ---------------------------------------------------------------------------


def _append_resolution(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    candidate: AgentCandidate,
    resolution: _Resolution,
    allocate_event_no,
    llm_fn,
) -> int:
    segments = _segments_by_labels(conn, resolution.segment_ids)
    speaker_by_label = {s.label: s.speaker for s in segments}
    origin = correct_origin(None, resolution.segment_ids, speaker_by_label)
    extracted_event = _as_extracted_event(candidate, resolution, origin)

    # 置き換えの出来事にも裏付けの検査をかける（設計書 §5.3 (7)）。
    support_output = check_support(
        SupportInput(items=[SupportItem(event=extracted_event, segments=segments)]), llm_fn
    )
    support = support_output.results[0] if support_output.results else "unsupported"

    states = visibility.fetch_source_states_for_labels(
        conn, project_id=project_id, labels=resolution.segment_ids
    )
    visibility_at_creation: SourceVisibility = (
        "managers_only" if any(v == "managers_only" for _, v in states) else "all"
    )

    event_no = allocate_event_no(conn, project_id)
    summary = resolution.summary or candidate.summary
    conn.execute(
        """
        INSERT INTO events
            (project_id, run_id, event_no, kind, summary, reason, occurred_at,
             segment_ids, origin, visibility_at_creation, support,
             supersedes_event_id, partition)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            project_id,
            run_id,
            event_no,
            candidate.kind,
            summary,
            resolution.reason,
            candidate.occurred_at,
            resolution.segment_ids,
            origin,
            visibility_at_creation,
            support,
            candidate.event_id,
            candidate.partition,
        ),
    )
    if resolution.segment_ids:
        conn.execute(
            "UPDATE source_segments SET consumed = true WHERE project_id = %s AND label = ANY(%s)",
            (project_id, resolution.segment_ids),
        )
    return event_no


def _as_extracted_event(
    candidate: AgentCandidate, resolution: _Resolution, origin: str | None
) -> ExtractedEvent:
    return ExtractedEvent(
        kind=candidate.kind,
        summary=resolution.summary or candidate.summary,
        reason=resolution.reason,
        occurred_at=candidate.occurred_at,
        segment_ids=resolution.segment_ids,
        origin=origin,
        supersedes_event_no=None,
        conflicts_with_event_no=None,
    )


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def _segments_by_labels(conn: psycopg.Connection, labels: list[str]) -> list[SegmentIn]:
    if not labels:
        return []
    rows = conn.execute(
        "SELECT label, speaker, seq, text FROM source_segments WHERE label = ANY(%s)",
        (labels,),
    ).fetchall()
    by_label = {r[0]: r for r in rows}
    result: list[SegmentIn] = []
    for label in labels:
        row = by_label.get(label)
        if row is None:
            continue
        result.append(SegmentIn(label=row[0], speaker=row[1], recorded_at=_now(), text=row[3]))
    return result


def _now() -> datetime:
    return datetime.now(UTC)


def _clamp_int(value: object, *, default: int) -> int:
    if not isinstance(value, int):
        return default
    return max(0, min(_MAX_READ_SEGMENTS, value))


def _project_id_of(conn: psycopg.Connection, event_id: UUID) -> UUID:
    row = conn.execute("SELECT project_id FROM events WHERE id = %s", (event_id,)).fetchone()
    return row[0]
