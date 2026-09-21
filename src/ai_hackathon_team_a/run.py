"""実行の本体（設計書 §5.1〜§5.3）。

W15（``POST /internal/runs/{rid}/execute``）から呼ばれる。DB とアドバイザリ
ロックに触れるのはこのモジュールだけで、抽出・裏付け・組み立て・検査・図の
中身はすべて ``pipeline/`` の純粋関数に委ねる。

エージェントのループ（(7)）・質問、メール・通知（(12)）は7段目で実装する。
ここでは ``investigating``・``notifying`` の段階を通過するだけにする。
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
import psycopg.types.json

from ai_hackathon_team_a import visibility
from ai_hackathon_team_a.authz import Visibility as SourceVisibility
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import CostLimitExceeded, LlmResult, RunContext, call_llm
from ai_hackathon_team_a.pipeline.contracts import (
    BASELINE_HEADINGS as _BASELINE_HEADINGS,
)
from ai_hackathon_team_a.pipeline.contracts import (
    DIFF_HEADINGS,
    AssembleInput,
    AssembleOutput,
    EventKind,
    EventSummary,
    ExtractedEvent,
    ExtractInput,
    Origin,
    SegmentIn,
    SupportInput,
    SupportItem,
    chunk_segments,
)
from ai_hackathon_team_a.pipeline.extract import extract_events
from ai_hackathon_team_a.pipeline.judge import assemble_and_judge
from ai_hackathon_team_a.pipeline.mermaid import MermaidEvent, build_mermaid
from ai_hackathon_team_a.pipeline.render import RenderEvent, render_markdown
from ai_hackathon_team_a.pipeline.support import check_support
from ai_hackathon_team_a.worker_settings import WorkerSettings

_RUN_TIMEOUT_SECONDS = 240
_PROGRESS_KINDS: frozenset[EventKind] = frozenset(
    {"decision", "rejected_option", "open_issue", "finding"}
)

Outcome = Literal["report_created", "no_new_events", "locked", "cost_limited", "failed"]
Partition = Literal["all", "managers"]
Audience = Literal["all", "managers"]


class RunTimeoutError(RuntimeError):
    """1回の実行が240秒を超えたことを表す（設計書 §5.1）。"""


@dataclass(frozen=True)
class RunOutcome:
    """``execute_run`` の戻り値（W15 の応答の元になる）。"""

    outcome: Outcome
    version_no: int | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class _EventRow:
    """DB に既にある1件の出来事（今も有効かどうかの判定に使う）。"""

    id: UUID
    event_no: int
    kind: EventKind
    summary: str
    reason: str | None
    occurred_at: datetime
    segment_ids: list[str]
    origin: Origin | None
    support: str | None
    supersedes_event_id: UUID | None
    partition: Partition


@dataclass
class _ExtractedWithProvisional:
    """抽出直後、まだ DB に無い1件の出来事（負の仮番号で参照し合う）。"""

    provisional_no: int
    event: ExtractedEvent


@dataclass
class _PartitionExtractionResult:
    extracted: list[_ExtractedWithProvisional] = field(default_factory=list)
    suspected_injection_segment_ids: set[str] = field(default_factory=set)


def execute_run(
    run_id: UUID,
    *,
    worker_settings: WorkerSettings,
    pool: ConnectionPool,
    llm_call=call_llm,
) -> RunOutcome:
    """W15 の実体。ロックを取り、段階を進め、outcome を返す（設計書 §5.1〜§5.3）。

    ``llm_call`` は ``call_llm`` と同じ形（``call_llm(stage, messages, *, run,
    json_mode, tools=None)``）の差し替え口で、テストで偽の実装に置き換えられる
    （本物を呼ぶのは常に ``llm.call_llm`` の1か所という原則は変わらない）。
    """

    with pool.connection() as conn:
        row = conn.execute("SELECT project_id FROM runs WHERE id = %s", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"run {run_id} not found")
    project_id: UUID = row[0]

    lock_key = f"decision-trace:{project_id}"
    lock_conn = psycopg.connect(worker_settings.database_url, autocommit=True)
    try:
        locked = lock_conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (lock_key,)
        ).fetchone()[0]
        if not locked:
            # 出来事やレポートは書かないが、run 自体は閉じる。queued のまま残すと、
            # 画面の状態問い合わせ（W16）がいつまでも終わりを知れないため。
            with pool.connection() as conn:
                conn.execute(
                    "UPDATE runs SET status = 'done', outcome = 'locked', finished_at = now() "
                    "WHERE id = %s AND status = 'queued'",
                    (run_id,),
                )
            return RunOutcome(outcome="locked")

        return _execute_locked(run_id, project_id=project_id, pool=pool, llm_call=llm_call)
    finally:
        try:
            lock_conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_key,))
        finally:
            lock_conn.close()


def _execute_locked(
    run_id: UUID, *, project_id: UUID, pool: ConnectionPool, llm_call
) -> RunOutcome:
    deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS

    with pool.connection() as conn:
        updated = conn.execute(
            "UPDATE runs SET status = 'running', started_at = now() "
            "WHERE id = %s AND status = 'queued' RETURNING id",
            (run_id,),
        ).fetchone()
    if updated is None:
        # 既に走った（同じ run_id の二重送信）。今の状態をそのまま返す（何も書き直さない）。
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT status, outcome, "
                "(SELECT MAX(version_no) FROM reports WHERE run_id = runs.id) "
                "FROM runs WHERE id = %s",
                (run_id,),
            ).fetchone()
        if row is not None and row[1] is not None:
            return RunOutcome(outcome=row[1], version_no=row[2])
        return RunOutcome(outcome="locked")

    run = RunContext(project_id=project_id, run_id=run_id)

    def llm_fn(
        stage: str,
        messages: list[dict[str, object]],
        *,
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> LlmResult:
        return llm_call(stage, messages, run=run, json_mode=json_mode, tools=tools)

    try:
        return _run_stages(
            run_id, project_id=project_id, pool=pool, llm_fn=llm_fn, deadline=deadline
        )
    except CostLimitExceeded:
        _finish_run(pool, run_id, status="failed", outcome="cost_limited")
        return RunOutcome(outcome="cost_limited")
    except RunTimeoutError:
        _finish_run(pool, run_id, status="failed", outcome="failed", error_message="timeout")
        return RunOutcome(outcome="failed", error_message="timeout")
    except Exception as exc:  # noqa: BLE001 - 実行全体を失敗として記録して終える
        message = str(exc)[:500]
        _finish_run(pool, run_id, status="failed", outcome="failed", error_message=message)
        return RunOutcome(outcome="failed", error_message=message)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise RunTimeoutError


def _run_stages(
    run_id: UUID,
    *,
    project_id: UUID,
    pool: ConnectionPool,
    llm_fn,
    deadline: float,
) -> RunOutcome:
    _set_step(pool, run_id, "extracting")

    with pool.connection() as conn:
        goal_description = _fetch_goal_description(conn, project_id)
        all_segments, managers_segments = _fetch_input_segments(conn, project_id)
        active_by_no = _fetch_active_events_by_partition_input(conn, project_id)

    new_event_nos: list[int] = []
    suspected_injection_by_partition: dict[Partition, set[str]] = {"all": set(), "managers": set()}

    with pool.connection() as conn:
        for partition, segments in (("all", all_segments), ("managers", managers_segments)):
            _check_deadline(deadline)
            if not segments:
                continue
            pre_existing = active_by_no.get(partition, [])
            result = _extract_partition(
                segments, pre_existing, llm_fn, deadline, goal_description=goal_description
            )
            suspected_injection_by_partition[partition] |= result.suspected_injection_segment_ids
            # 出来事が0件でも、消費されなかった区切りの carry_count は進める（§5.3 (6)）。
            appended = _persist_partition(
                conn,
                project_id=project_id,
                run_id=run_id,
                partition=partition,
                segments=segments,
                result=result,
                llm_fn=llm_fn,
            )
            new_event_nos.extend(appended)
        conn.commit()

    _set_step(pool, run_id, "investigating")  # エージェント・質問は7段目で実装する。

    with pool.connection() as conn:
        project_row = conn.execute(
            "SELECT needs_rebuild FROM projects WHERE id = %s", (project_id,)
        ).fetchone()
    needs_rebuild = bool(project_row[0]) if project_row else False

    progress = any(
        kind in _PROGRESS_KINDS
        for kind in _fetch_kinds_for_event_nos(pool, project_id, new_event_nos)
    )

    if not progress and not needs_rebuild:
        _finish_run(pool, run_id, status="done", outcome="no_new_events")
        return RunOutcome(outcome="no_new_events")

    _set_step(pool, run_id, "assembling")
    _check_deadline(deadline)

    with pool.connection() as conn:
        all_events, state_map = _fetch_all_events_and_states(conn, project_id)

    active_all = _active_for_audience(all_events, state_map, "all")
    active_managers = _active_for_audience(all_events, state_map, "managers")
    build_managers_report = any(
        visibility.effective_visibility(
            partition=e.partition, source_states=_states_for(e.segment_ids, state_map)
        )
        == "managers_only"
        for e in active_managers
    )

    with pool.connection() as conn:
        version_no = _allocate_version_no(conn, project_id)
        conn.commit()

    _set_step(pool, run_id, "checking")
    _check_deadline(deadline)

    with pool.connection() as conn:
        _build_and_store_report(
            conn,
            project_id=project_id,
            run_id=run_id,
            audience="all",
            version_no=version_no,
            goal_description=goal_description,
            active_events=active_all,
            new_event_nos=set(new_event_nos),
            llm_fn=llm_fn,
            suspected_injection_labels=suspected_injection_by_partition["all"],
        )
        if build_managers_report:
            _build_and_store_report(
                conn,
                project_id=project_id,
                run_id=run_id,
                audience="managers",
                version_no=version_no,
                goal_description=goal_description,
                active_events=active_managers,
                new_event_nos=set(new_event_nos),
                llm_fn=llm_fn,
                suspected_injection_labels=(
                    suspected_injection_by_partition["all"]
                    | suspected_injection_by_partition["managers"]
                ),
            )
        conn.execute("UPDATE projects SET needs_rebuild = false WHERE id = %s", (project_id,))
        conn.commit()

    _set_step(pool, run_id, "notifying")  # メール・アプリ内通知は7段目で実装する。

    _finish_run(pool, run_id, status="done", outcome="report_created")
    return RunOutcome(outcome="report_created", version_no=version_no)


# ---------------------------------------------------------------------------
# 段階の記録
# ---------------------------------------------------------------------------


def _set_step(pool: ConnectionPool, run_id: UUID, step: str) -> None:
    """``runs.step`` を別トランザクションで確定する（設計書 §5.1）。"""

    with pool.connection() as conn:
        conn.execute("UPDATE runs SET step = %s WHERE id = %s", (step, run_id))


def _finish_run(
    pool: ConnectionPool,
    run_id: UUID,
    *,
    status: str,
    outcome: str,
    error_message: str | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE runs SET status = %s, outcome = %s, error_message = %s, "
            "step = 'done', finished_at = now() WHERE id = %s",
            (status, outcome, error_message, run_id),
        )


# ---------------------------------------------------------------------------
# (1) 入力の区切りを集める・分ける
# ---------------------------------------------------------------------------


def _fetch_goal_description(conn: psycopg.Connection, project_id: UUID) -> str:
    row = conn.execute(
        "SELECT goal_description FROM projects WHERE id = %s", (project_id,)
    ).fetchone()
    return row[0] if row else ""


def _fetch_input_segments(
    conn: psycopg.Connection, project_id: UUID
) -> tuple[list[SegmentIn], list[SegmentIn]]:
    """除外以外・現在の版・未消費の区切りを、可視性で all / managers に分ける（§5.3 (1)(2)）。"""

    rows = conn.execute(
        """
        SELECT seg.label, seg.speaker, ps.recorded_at, seg.text, ps.visibility
        FROM source_segments seg
        JOIN source_versions sv ON sv.id = seg.source_version_id
        JOIN project_sources ps ON ps.id = sv.source_id
        WHERE seg.project_id = %s
          AND ps.is_excluded = false
          AND sv.id = ps.current_version_id
          AND seg.consumed = false
        ORDER BY ps.source_no, seg.seq
        """,
        (project_id,),
    ).fetchall()

    all_segments: list[SegmentIn] = []
    managers_segments: list[SegmentIn] = []
    for label, speaker, recorded_at, text, source_visibility in rows:
        segment = SegmentIn(label=label, speaker=speaker, recorded_at=recorded_at, text=text)
        if source_visibility == "managers_only":
            managers_segments.append(segment)
        else:
            all_segments.append(segment)
    return all_segments, managers_segments


def _fetch_active_events_by_partition_input(
    conn: psycopg.Connection, project_id: UUID
) -> dict[Partition, list[EventSummary]]:
    """抽出に見せる「今も有効な出来事」を、組ごとに絞って返す（§5.3 (2)(3)）。"""

    all_events, state_map = _fetch_all_events_and_states(conn, project_id)
    active_all = _active_for_audience(all_events, state_map, "all")
    active_managers = _active_for_audience(all_events, state_map, "managers")

    return {
        "all": [_to_event_summary(e) for e in active_all],
        # managers の組は、今も有効な出来事をすべて見る（§5.3 (2)）。
        "managers": [_to_event_summary(e) for e in active_managers],
    }


def _to_event_summary(row: _EventRow) -> EventSummary:
    return EventSummary(
        event_no=row.event_no, kind=row.kind, summary=row.summary, reason=row.reason
    )


# ---------------------------------------------------------------------------
# (3)(4) 抽出・番号の検査・出どころの補正
# ---------------------------------------------------------------------------


def _extract_partition(
    segments: list[SegmentIn],
    pre_existing_active: list[EventSummary],
    llm_fn,
    deadline: float,
    *,
    goal_description: str,
) -> _PartitionExtractionResult:
    result = _PartitionExtractionResult()
    provisional_events: list[EventSummary] = []
    next_provisional_no = -1

    for chunk in chunk_segments(segments, limit=6000):
        _check_deadline(deadline)
        chunk_input = ExtractInput(
            goal_description=goal_description,
            segments=chunk,
            active_events=[*pre_existing_active, *provisional_events],
        )
        output = extract_events(chunk_input, llm_fn)
        result.suspected_injection_segment_ids.update(output.suspected_injection_segment_ids)

        for event in output.events:
            provisional_no = next_provisional_no
            next_provisional_no -= 1
            result.extracted.append(
                _ExtractedWithProvisional(provisional_no=provisional_no, event=event)
            )
            provisional_events.append(
                EventSummary(
                    event_no=provisional_no,
                    kind=event.kind,
                    summary=event.summary,
                    reason=event.reason,
                )
            )

    return result


# ---------------------------------------------------------------------------
# (5)(6) 裏付けの検査・出来事の追記と持ち越し
# ---------------------------------------------------------------------------


def _persist_partition(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    partition: Partition,
    segments: list[SegmentIn],
    result: _PartitionExtractionResult,
    llm_fn,
) -> list[int]:
    segment_by_label = {s.label: s for s in segments}
    state_map = _fetch_all_segment_states(conn, project_id)

    support_input = SupportInput(
        items=[
            SupportItem(
                event=item.event,
                segments=[
                    segment_by_label[sid]
                    for sid in item.event.segment_ids
                    if sid in segment_by_label
                ],
            )
            for item in result.extracted
        ]
    )
    support_output = check_support(support_input, llm_fn)

    real_active_by_no = _fetch_real_active_event_ids(conn, project_id)
    provisional_to_real: dict[int, UUID] = {}

    used_labels: set[str] = set()
    new_event_nos: list[int] = []

    for item, support in zip(result.extracted, support_output.results, strict=True):
        event = item.event
        used_labels.update(event.segment_ids)

        supersedes_event_id: UUID | None = None
        if event.supersedes_event_no is not None:
            if event.supersedes_event_no < 0:
                supersedes_event_id = provisional_to_real.get(event.supersedes_event_no)
            else:
                supersedes_event_id = real_active_by_no.get(event.supersedes_event_no)

        source_states = _states_for(event.segment_ids, state_map)
        visibility_at_creation: SourceVisibility = (
            "managers_only" if any(vis == "managers_only" for _, vis in source_states) else "all"
        )

        event_no = _allocate_event_no(conn, project_id)
        row = conn.execute(
            """
            INSERT INTO events
                (project_id, run_id, event_no, kind, summary, reason, occurred_at,
                 segment_ids, origin, visibility_at_creation, support,
                 supersedes_event_id, partition)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                project_id,
                run_id,
                event_no,
                event.kind,
                event.summary,
                event.reason,
                event.occurred_at,
                event.segment_ids,
                event.origin,
                visibility_at_creation,
                support,
                supersedes_event_id,
                partition,
            ),
        ).fetchone()
        provisional_to_real[item.provisional_no] = row[0]
        new_event_nos.append(event_no)

    all_labels = {s.label for s in segments}
    carried_labels = sorted(all_labels - used_labels)
    if used_labels:
        conn.execute(
            "UPDATE source_segments SET consumed = true WHERE project_id = %s AND label = ANY(%s)",
            (project_id, sorted(used_labels)),
        )
    if carried_labels:
        conn.execute(
            "UPDATE source_segments SET carry_count = carry_count + 1 "
            "WHERE project_id = %s AND label = ANY(%s)",
            (project_id, carried_labels),
        )
        conn.execute(
            "UPDATE source_segments SET consumed = true "
            "WHERE project_id = %s AND label = ANY(%s) AND carry_count >= 3",
            (project_id, carried_labels),
        )

    return new_event_nos


def _fetch_real_active_event_ids(conn: psycopg.Connection, project_id: UUID) -> dict[int, UUID]:
    rows = conn.execute(
        "SELECT event_no, id FROM events WHERE project_id = %s", (project_id,)
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _allocate_event_no(conn: psycopg.Connection, project_id: UUID) -> int:
    row = conn.execute(
        "SELECT next_event_no FROM projects WHERE id = %s FOR UPDATE", (project_id,)
    ).fetchone()
    event_no: int = row[0]
    conn.execute(
        "UPDATE projects SET next_event_no = next_event_no + 1 WHERE id = %s", (project_id,)
    )
    return event_no


def _allocate_version_no(conn: psycopg.Connection, project_id: UUID) -> int:
    row = conn.execute(
        "SELECT next_version_no FROM projects WHERE id = %s FOR UPDATE", (project_id,)
    ).fetchone()
    version_no: int = row[0]
    conn.execute(
        "UPDATE projects SET next_version_no = next_version_no + 1 WHERE id = %s", (project_id,)
    )
    return version_no


def _fetch_kinds_for_event_nos(
    pool: ConnectionPool, project_id: UUID, event_nos: list[int]
) -> list[EventKind]:
    if not event_nos:
        return []
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT kind FROM events WHERE project_id = %s AND event_no = ANY(%s)",
            (project_id, event_nos),
        ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# 実効の可視性・「今も有効」の判定（§5.5、§5.3 (2)）
# ---------------------------------------------------------------------------


def _fetch_all_segment_states(
    conn: psycopg.Connection, project_id: UUID
) -> dict[str, tuple[bool, SourceVisibility]]:
    rows = conn.execute(
        """
        SELECT seg.label, ps.is_excluded, ps.visibility
        FROM source_segments seg
        JOIN source_versions sv ON sv.id = seg.source_version_id
        JOIN project_sources ps ON ps.id = sv.source_id
        WHERE seg.project_id = %s
        """,
        (project_id,),
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def _states_for(
    segment_ids: list[str], state_map: dict[str, tuple[bool, SourceVisibility]]
) -> list[tuple[bool, SourceVisibility]]:
    return [state_map[sid] for sid in segment_ids if sid in state_map]


def _fetch_all_events_and_states(
    conn: psycopg.Connection, project_id: UUID
) -> tuple[list[_EventRow], dict[str, tuple[bool, SourceVisibility]]]:
    rows = conn.execute(
        """
        SELECT id, event_no, kind, summary, reason, occurred_at, segment_ids, origin,
               support, supersedes_event_id, partition
        FROM events
        WHERE project_id = %s
        """,
        (project_id,),
    ).fetchall()
    events = [
        _EventRow(
            id=row[0],
            event_no=row[1],
            kind=row[2],
            summary=row[3],
            reason=row[4],
            occurred_at=row[5],
            segment_ids=list(row[6] or []),
            origin=row[7],
            support=row[8],
            supersedes_event_id=row[9],
            partition=row[10],
        )
        for row in rows
    ]
    state_map = _fetch_all_segment_states(conn, project_id)
    return events, state_map


def _active_for_audience(
    events: list[_EventRow],
    state_map: dict[str, tuple[bool, SourceVisibility]],
    audience: Audience,
) -> list[_EventRow]:
    """audience から見た「今も有効な出来事」（§5.3 (2)、§5.5、【C-5】【B-X1】）。"""

    eligible: dict[UUID, _EventRow] = {}
    for event in events:
        eff = visibility.effective_visibility(
            partition=event.partition, source_states=_states_for(event.segment_ids, state_map)
        )
        if eff == "excluded":
            continue
        if audience == "all" and eff != "all":
            continue
        eligible[event.id] = event

    superseded_ids = {
        event.supersedes_event_id
        for event in eligible.values()
        if event.supersedes_event_id is not None and event.supersedes_event_id in eligible
    }

    return sorted(
        (event for event_id, event in eligible.items() if event_id not in superseded_ids),
        key=lambda e: e.occurred_at,
    )


# ---------------------------------------------------------------------------
# (9)(10)(11) レポートの組み立て・検査・図
# ---------------------------------------------------------------------------


def _build_and_store_report(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    audience: Audience,
    version_no: int,
    goal_description: str,
    active_events: list[_EventRow],
    new_event_nos: set[int],
    llm_fn,
    suspected_injection_labels: set[str],
) -> None:
    existing_reports = conn.execute(
        "SELECT 1 FROM reports WHERE project_id = %s AND audience = %s LIMIT 1",
        (project_id, audience),
    ).fetchone()
    mode: Literal["diff", "baseline"] = "baseline" if existing_reports is None else "diff"
    headings = _BASELINE_HEADINGS if mode == "baseline" else DIFF_HEADINGS

    event_summaries = [_to_event_summary(e) for e in active_events]
    valid_event_nos = {e.event_no for e in active_events}
    unsupported_event_nos = {e.event_no for e in active_events if e.support == "unsupported"}
    audience_new_event_nos = sorted(valid_event_nos & new_event_nos)

    assemble_input = AssembleInput(
        mode=mode,
        goal_description=goal_description,
        events=event_summaries,
        new_event_nos=audience_new_event_nos,
    )
    result = assemble_and_judge(
        assemble_input,
        llm_fn,
        headings=headings,
        valid_event_nos=valid_event_nos,
        unsupported_event_nos=unsupported_event_nos,
    )

    render_events = [
        RenderEvent(event_no=e.event_no, segment_ids=e.segment_ids, origin=e.origin)
        for e in active_events
    ]
    checked_output = AssembleOutput(
        sections=result.check.sections, summary_for_mail=result.output.summary_for_mail
    )
    rendered = render_markdown(checked_output, render_events, headings=headings)

    mermaid_events = [
        MermaidEvent(
            event_no=e.event_no,
            kind=e.kind,
            summary=e.summary,
            occurred_at=e.occurred_at,
            supersedes_event_no=_supersedes_event_no(e, active_events),
        )
        for e in active_events
    ]
    mermaid_dsl = build_mermaid(mermaid_events)

    input_segment_ids = sorted({sid for e in active_events for sid in e.segment_ids})
    cited_labels = sorted({sid for labels in rendered.evidence_catalog.values() for sid in labels})

    flags: dict[str, object] = {
        "suspected_injection": sorted(suspected_injection_labels & set(input_segment_ids)),
        "unverified_ai_count": rendered.unverified_ai_count,
    }

    conn.execute(
        """
        INSERT INTO reports
            (project_id, run_id, version_no, audience, kind, judge_status, body_markdown,
             mermaid_dsl, evidence_catalog, event_ids, cited_segment_ids, input_segment_ids,
             flags, summary_for_mail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            project_id,
            run_id,
            version_no,
            audience,
            mode,
            result.judge_status,
            rendered.body_markdown,
            mermaid_dsl,
            _to_jsonb(rendered.evidence_catalog),
            [e.event_no for e in active_events],
            cited_labels,
            input_segment_ids,
            _to_jsonb(flags),
            result.output.summary_for_mail,
        ),
    )


def _supersedes_event_no(event: _EventRow, all_active: list[_EventRow]) -> int | None:
    if event.supersedes_event_id is None:
        return None
    for other in all_active:
        if other.id == event.supersedes_event_id:
            return other.event_no
    return None


def _to_jsonb(value: dict) -> psycopg.types.json.Json:
    return psycopg.types.json.Json(value)
