"""実行の本体（設計書 §5.1〜§5.3）。

W15（``POST /internal/runs/{rid}/execute``）から呼ばれる。DB とアドバイザリ
ロックに触れるのはこのモジュールだけで、抽出・裏付け・組み立て・検査・図の
中身はすべて ``pipeline/`` の純粋関数に委ねる。エージェントのループ（(7)）は
``agent.py``、メール・アプリ内通知（(12)）は ``notify.py`` に委ねる。
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
import psycopg
import psycopg.types.json

from ai_hackathon_team_a import agent, notify, visibility
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

logger = logging.getLogger(__name__)

_RUN_TIMEOUT_SECONDS = 240
_PROGRESS_KINDS: frozenset[EventKind] = frozenset(
    {"decision", "rejected_option", "open_issue", "finding"}
)

# W16 の error_message に出す、利用者向けの定型の日本語（設計書 §8 I1）。例外の
# 詳細（接続情報等を含みうる）はサーバーのログにだけ残し、API には出さない。
_GENERIC_ERROR_MESSAGE = "実行中にエラーが起きました。"
_TIMEOUT_ERROR_MESSAGE = "時間内に終わりませんでした。"

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
    # 送る直前の再確認【B-X2】：レポートを保存する直前に visibility_epoch が実行開始時
    # から変わっていなければ True。False のときは、レポートは保存したがメール・
    # アプリ内通知（7段目）を送ってはいけないという印（needs_rebuild も true のまま）。
    notify_allowed: bool = True


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
    # AD-10：過去に却下した案との類似（decision/finding/open_issue だけに付く）。
    similar_rejected_event_no: int | None = None


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
    mail_transport: httpx.BaseTransport | None = None,
) -> RunOutcome:
    """W15 の実体。run を引き受け、ロックを取り、段階を進め、outcome を返す
    （設計書 §5.1〜§5.3）。

    ``llm_call`` は ``call_llm`` と同じ形（``call_llm(stage, messages, *, run,
    json_mode, tools=None)``）の差し替え口で、テストで偽の実装に置き換えられる
    （本物を呼ぶのは常に ``llm.call_llm`` の1か所という原則は変わらない）。
    ``mail_transport`` はテストで ``httpx.MockTransport`` に差し替える口。

    **順序が重要**：まず ``queued → running`` への引き受け（``UPDATE ... WHERE
    status = 'queued'``）を先に行い、それに成功した側だけがアドバイザリロックを
    取りに行く。先にロックを試みて「取れなかった側が queued の run を閉じる」
    順序だと、同じ run_id の二重送信で、ロックを取れた側が引き受ける前に
    もう一方が同じ run を ``locked`` で閉じてしまい、両方が ``locked`` を返す
    事故が起きる（引き受けを先に行えば、二重送信のどちらか一方しか
    ``status='queued'`` の更新に成功しないため、この事故が起きない）。
    """

    with pool.connection() as conn:
        row = conn.execute(
            "SELECT project_id, trigger FROM runs WHERE id = %s", (run_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"run {run_id} not found")
    project_id: UUID = row[0]
    trigger: str = row[1]

    with pool.connection() as conn:
        claimed = conn.execute(
            "UPDATE runs SET status = 'running', started_at = now() "
            "WHERE id = %s AND status = 'queued' RETURNING id",
            (run_id,),
        ).fetchone()
    if claimed is None:
        # 既に走った（同じ run_id の二重送信）。今の状態をそのまま返す（何も書き直さない）。
        return _stored_outcome(pool, run_id)

    lock_key = f"decision-trace:{project_id}"
    lock_conn = psycopg.connect(worker_settings.database_url, autocommit=True)
    try:
        locked = lock_conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (lock_key,)
        ).fetchone()[0]
        if not locked:
            # 自分が引き受けた run だけを閉じる（他の実行の run には触れない）。
            _finish_run(pool, run_id, status="done", outcome="locked")
            return RunOutcome(outcome="locked")

        return _run_claimed(
            run_id,
            project_id=project_id,
            trigger=trigger,
            pool=pool,
            llm_call=llm_call,
            worker_settings=worker_settings,
            mail_transport=mail_transport,
        )
    finally:
        try:
            lock_conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_key,))
        finally:
            lock_conn.close()


def _stored_outcome(pool: ConnectionPool, run_id: UUID) -> RunOutcome:
    """引き受けられなかった run（二重送信）の、今の状態をそのまま返す。"""

    with pool.connection() as conn:
        row = conn.execute(
            "SELECT outcome, "
            "(SELECT MAX(version_no) FROM reports WHERE run_id = runs.id) "
            "FROM runs WHERE id = %s",
            (run_id,),
        ).fetchone()
    if row is not None and row[0] is not None:
        return RunOutcome(outcome=row[0], version_no=row[1])
    return RunOutcome(outcome="locked")


def _run_claimed(
    run_id: UUID,
    *,
    project_id: UUID,
    trigger: str,
    pool: ConnectionPool,
    llm_call,
    worker_settings: WorkerSettings,
    mail_transport: httpx.BaseTransport | None,
) -> RunOutcome:
    """run を引き受け、ロックも取れた実行の本体。"""

    deadline = time.monotonic() + _RUN_TIMEOUT_SECONDS
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
            run_id,
            project_id=project_id,
            allow_regeneration=(trigger == "schedule"),
            pool=pool,
            llm_fn=llm_fn,
            deadline=deadline,
            worker_settings=worker_settings,
            mail_transport=mail_transport,
        )
    except CostLimitExceeded:
        with pool.connection() as conn:
            notify.notify_cost_limited_once_per_day(conn, project_id=project_id)
        _finish_run(pool, run_id, status="failed", outcome="cost_limited")
        return RunOutcome(outcome="cost_limited")
    except RunTimeoutError:
        _finish_run(
            pool, run_id, status="failed", outcome="failed", error_message=_TIMEOUT_ERROR_MESSAGE
        )
        return RunOutcome(outcome="failed", error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception:  # noqa: BLE001 - 実行全体を失敗として記録して終える
        # 例外の詳細（秘密・接続情報を含みうる）はログにだけ残し、API には出さない
        # （設計書 §8 I1：W16 の error_message は定型の日本語にする）。
        logger.exception("run %s failed", run_id)
        _finish_run(
            pool, run_id, status="failed", outcome="failed", error_message=_GENERIC_ERROR_MESSAGE
        )
        return RunOutcome(outcome="failed", error_message=_GENERIC_ERROR_MESSAGE)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise RunTimeoutError


def epoch_unchanged(conn: psycopg.Connection, project_id: UUID, epoch: int) -> bool:
    """送る直前の再確認【B-X2】。

    実行の開始時に控えた ``visibility_epoch`` が、今の値と一致するかを確かめる。
    レポートを保存する直前にここで呼び、7段目（メール・アプリ内通知）は、送る
    直前にもう一度この関数を呼んでから送る前提（可視性・除外・所属・ロールが
    実行中に変わっていたら、メール・通知の内容が古くなっている可能性があるため）。
    """

    row = conn.execute(
        "SELECT visibility_epoch FROM projects WHERE id = %s", (project_id,)
    ).fetchone()
    return row is not None and row[0] == epoch


def _run_stages(
    run_id: UUID,
    *,
    project_id: UUID,
    allow_regeneration: bool,
    pool: ConnectionPool,
    llm_fn,
    deadline: float,
    worker_settings: WorkerSettings,
    mail_transport: httpx.BaseTransport | None,
) -> RunOutcome:
    _set_step(pool, run_id, "extracting")

    with pool.connection() as conn:
        # 送る直前の再確認【B-X2】のため、実行の開始時に visibility_epoch を控える。
        start_epoch = conn.execute(
            "SELECT visibility_epoch FROM projects WHERE id = %s", (project_id,)
        ).fetchone()[0]
        goal_description = _fetch_goal_description(conn, project_id)
        all_segments, managers_segments = _fetch_input_segments(conn, project_id)
        active_input = _fetch_active_events_by_partition_input(conn, project_id)

    new_event_nos: list[int] = []
    suspected_injection_by_partition: dict[Partition, set[str]] = {"all": set(), "managers": set()}
    agent_candidates: list[agent.AgentCandidate] = []

    with pool.connection() as conn:
        for partition, segments in (("all", all_segments), ("managers", managers_segments)):
            _check_deadline(deadline)
            if not segments:
                continue
            pre_existing = active_input.summaries.get(partition, [])
            past_rejected = active_input.past_rejected.get(partition, [])
            result = _extract_partition(
                segments,
                pre_existing,
                llm_fn,
                deadline,
                goal_description=goal_description,
                past_rejected_events=past_rejected,
            )
            suspected_injection_by_partition[partition] |= result.suspected_injection_segment_ids
            # 出来事が0件でも、消費されなかった区切りの carry_count は進める（§5.3 (6)）。
            persisted = _persist_partition(
                conn,
                project_id=project_id,
                run_id=run_id,
                partition=partition,
                segments=segments,
                result=result,
                llm_fn=llm_fn,
                existing_dedup_keys=active_input.dedup_keys.get(partition, set()),
            )
            new_event_nos.extend(persisted.new_event_nos)
            agent_candidates.extend(persisted.agent_candidates)
        conn.commit()

    _set_step(pool, run_id, "investigating")

    if agent_candidates:
        _check_deadline(deadline)
        with pool.connection() as conn:
            resolved = agent.run_agent_for_candidates(
                conn,
                project_id=project_id,
                run_id=run_id,
                candidates=agent_candidates,
                llm_fn=llm_fn,
                goal_description=goal_description,
                start_epoch=start_epoch,
                worker_settings=worker_settings,
                allocate_event_no=_allocate_event_no,
                mail_transport=mail_transport,
            )
            new_event_nos.extend(r.event_no for r in resolved)
            conn.commit()

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

    _set_step(pool, run_id, "checking")
    _check_deadline(deadline)

    # レポートの中身（組み立て・Judge・図）は、トランザクションの外で作る。LLM の呼び出し
    # の間に projects の行をロックしていると、呼び出しの記録（model_calls_log）の書き込みが
    # 外部キーの確認でそのロックを待ち、自分自身を待ち続けてしまうため（2026-09-22 に実際に
    # 起きた）。保存は最後の短いトランザクションで、版の番号の払い出しと一緒に行う。
    with pool.connection() as conn:
        mode_all = _report_mode(conn, project_id, "all")
        mode_managers = _report_mode(conn, project_id, "managers")
    built_all = _build_report(
        project_id=project_id,
        mode=mode_all,
        goal_description=goal_description,
        audience="all",
        active_events=active_all,
        all_events=all_events,
        state_map=state_map,
        new_event_nos=set(new_event_nos),
        llm_fn=llm_fn,
        suspected_injection_labels=suspected_injection_by_partition["all"],
        allow_regeneration=allow_regeneration,
    )
    built_managers = (
        _build_report(
            project_id=project_id,
            mode=mode_managers,
            goal_description=goal_description,
            audience="managers",
            active_events=active_managers,
            all_events=all_events,
            state_map=state_map,
            new_event_nos=set(new_event_nos),
            llm_fn=llm_fn,
            suspected_injection_labels=(
                suspected_injection_by_partition["all"]
                | suspected_injection_by_partition["managers"]
            ),
            allow_regeneration=allow_regeneration,
        )
        if build_managers_report
        else None
    )
    _check_deadline(deadline)

    with pool.connection() as conn:
        # 版の番号は、保存するこのトランザクションの中で払い出す（保存しない実行は番号を
        # 使わない）。全員向けと管理者向けは同じ番号。
        version_no = _allocate_version_no(conn, project_id)

        # レポートを保存する直前に、もう一度 visibility_epoch を確かめる【B-X2】。
        notify_allowed = epoch_unchanged(conn, project_id, start_epoch)

        stored_all = _store_report(
            conn,
            built_all,
            project_id=project_id,
            run_id=run_id,
            audience="all",
            version_no=version_no,
        )
        if built_managers is not None:
            _store_report(
                conn,
                built_managers,
                project_id=project_id,
                run_id=run_id,
                audience="managers",
                version_no=version_no,
            )
        # epoch が変わっていたら、レポートは保存するが needs_rebuild は true のまま
        # にする（次の実行で今の状態から作り直す）。メール・通知を送るかどうかは
        # notify_allowed を見て次で決める。
        conn.execute(
            "UPDATE projects SET needs_rebuild = %s WHERE id = %s",
            (not notify_allowed, project_id),
        )
        conn.commit()

    _set_step(pool, run_id, "notifying")

    if notify_allowed and stored_all.had_new_events:
        with pool.connection() as conn:
            # 送る直前にもう一度確かめる【B-X2】（保存後にもう一度可視性が変わった場合）。
            if epoch_unchanged(conn, project_id, start_epoch):
                notify.send_progress_notifications(
                    conn,
                    project_id=project_id,
                    report_id=stored_all.report_id,
                    version_no=version_no,
                    summary_for_mail=stored_all.summary_for_mail,
                    settings=worker_settings,
                    transport=mail_transport,
                )
            else:
                conn.execute(
                    "UPDATE projects SET needs_rebuild = true WHERE id = %s", (project_id,)
                )
            conn.commit()

    _finish_run(pool, run_id, status="done", outcome="report_created")
    return RunOutcome(
        outcome="report_created", version_no=version_no, notify_allowed=notify_allowed
    )


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


@dataclass(frozen=True)
class _ActiveEventsByPartition:
    """「今も有効な出来事」を、組ごとの要約（抽出に見せる分）と重複検査の鍵の両方で持つ。

    鍵は ``(kind, segment_ids の集合)``。同じ組の今も有効な出来事と種類・根拠の区切りが
    どちらも一致する出来事は、抽出のたびに重複して追記されないようにする（設計書
    §5.3 (2)(3)、手元の確認で見つかった重複の修正）。
    """

    summaries: dict[Partition, list[EventSummary]]
    dedup_keys: dict[Partition, set[tuple[EventKind, frozenset[str]]]]
    # AD-10：同じ組で見える過去の rejected_option（置き換えられたものも含む。除外は除く）。
    past_rejected: dict[Partition, list[EventSummary]]


def _fetch_active_events_by_partition_input(
    conn: psycopg.Connection, project_id: UUID
) -> _ActiveEventsByPartition:
    """抽出に見せる「今も有効な出来事」を、組ごとに絞って返す（§5.3 (2)(3)）。"""

    all_events, state_map = _fetch_all_events_and_states(conn, project_id)
    active_all = _active_for_audience(all_events, state_map, "all")
    active_managers = _active_for_audience(all_events, state_map, "managers")

    return _ActiveEventsByPartition(
        summaries={
            "all": [_to_event_summary(e) for e in active_all],
            # managers の組は、今も有効な出来事をすべて見る（§5.3 (2)）。
            "managers": [_to_event_summary(e) for e in active_managers],
        },
        dedup_keys={
            "all": {(e.kind, frozenset(e.segment_ids)) for e in active_all},
            "managers": {(e.kind, frozenset(e.segment_ids)) for e in active_managers},
        },
        past_rejected={
            "all": _past_rejected_by_audience(all_events, state_map, "all"),
            "managers": _past_rejected_by_audience(all_events, state_map, "managers"),
        },
    )


def _past_rejected_by_audience(
    events: list[_EventRow],
    state_map: dict[str, tuple[bool, SourceVisibility]],
    audience: Audience,
) -> list[EventSummary]:
    """AD-10：組ごとに見える過去の ``rejected_option``（置き換えられたものも含む）。

    「今も有効」（``_active_for_audience``）とは違い、置き換え済みのものも残す
    （再浮上の検知は、過去に一度却下した案そのものと比べるため）。実効の可視性の
    規則は同じ（除外は除く。member 相当の ``all`` は実効の可視性が ``all`` の
    ものだけ）。
    """

    matched: list[_EventRow] = []
    for event in events:
        if event.kind != "rejected_option":
            continue
        eff = visibility.effective_visibility(
            partition=event.partition, source_states=_states_for(event.segment_ids, state_map)
        )
        if eff == "excluded":
            continue
        if audience == "all" and eff != "all":
            continue
        matched.append(event)
    return [_to_event_summary(e) for e in sorted(matched, key=lambda e: e.occurred_at)]


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
    past_rejected_events: list[EventSummary],
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
            past_rejected_events=past_rejected_events,
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


@dataclass(frozen=True)
class _PersistResult:
    """``_persist_partition`` の結果（設計書 §5.3 (6)(7)）。"""

    new_event_nos: list[int]
    agent_candidates: list[agent.AgentCandidate]


def _persist_partition(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    partition: Partition,
    segments: list[SegmentIn],
    result: _PartitionExtractionResult,
    llm_fn,
    existing_dedup_keys: set[tuple[EventKind, frozenset[str]]],
) -> _PersistResult:
    segment_by_label = {s.label: s for s in segments}
    state_map = _fetch_all_segment_states(conn, project_id)

    # 同じ組の今も有効な出来事と、種類・根拠の区切りの集合がどちらも一致する出来事は
    # 追記しない（持ち越しの区切りから同じ内容が再抽出される重複を防ぐ）。捨てた分の
    # 区切りは、既存の出来事がすでに同じ内容を表しているので使用済み扱いにする
    # （持ち越しにしない）。捨てた件数は測定用にログへ残す。
    seen_dedup_keys = set(existing_dedup_keys)
    kept_items: list[_ExtractedWithProvisional] = []
    used_labels: set[str] = set()
    dropped_duplicate_count = 0
    for item in result.extracted:
        key = (item.event.kind, frozenset(item.event.segment_ids))
        if key in seen_dedup_keys:
            dropped_duplicate_count += 1
            used_labels.update(item.event.segment_ids)
            continue
        seen_dedup_keys.add(key)
        kept_items.append(item)
    if dropped_duplicate_count:
        logger.info(
            "run %s: partition=%s で既存の出来事と重複する %d 件を捨てました",
            run_id,
            partition,
            dropped_duplicate_count,
        )

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
            for item in kept_items
        ]
    )
    support_output = check_support(support_input, llm_fn)

    real_active_by_no = _fetch_real_active_event_ids(conn, project_id)
    provisional_to_real: dict[int, UUID] = {}

    new_event_nos: list[int] = []
    agent_candidates: list[agent.AgentCandidate] = []

    for item, support in zip(kept_items, support_output.results, strict=True):
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
                 supersedes_event_id, partition, similar_rejected_event_no)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                event.similar_rejected_event_no,
            ),
        ).fetchone()
        provisional_to_real[item.provisional_no] = row[0]
        new_event_nos.append(event_no)

        if agent.needs_agent(
            kind=event.kind,
            reason=event.reason,
            supersedes_event_no=event.supersedes_event_no,
            conflicts_with_event_no=event.conflicts_with_event_no,
        ):
            agent_candidates.append(
                agent.AgentCandidate(
                    event_id=row[0],
                    event_no=event_no,
                    kind=event.kind,
                    summary=event.summary,
                    reason=event.reason,
                    occurred_at=event.occurred_at,
                    segment_ids=event.segment_ids,
                    partition=partition,
                )
            )

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

    return _PersistResult(new_event_nos=new_event_nos, agent_candidates=agent_candidates)


def _fetch_real_active_event_ids(conn: psycopg.Connection, project_id: UUID) -> dict[int, UUID]:
    rows = conn.execute(
        "SELECT event_no, id FROM events WHERE project_id = %s", (project_id,)
    ).fetchall()
    return {row[0]: row[1] for row in rows}


# 番号の払い出しは UPDATE … RETURNING で行う。SELECT … FOR UPDATE と違い、キーでない列の
# 更新のロック（FOR NO KEY UPDATE）で済むので、ほかの表からの外部キーの確認
# （FOR KEY SHARE）とぶつからない。


def _allocate_event_no(conn: psycopg.Connection, project_id: UUID) -> int:
    row = conn.execute(
        "UPDATE projects SET next_event_no = next_event_no + 1 WHERE id = %s "
        "RETURNING next_event_no - 1",
        (project_id,),
    ).fetchone()
    return int(row[0])


def _allocate_version_no(conn: psycopg.Connection, project_id: UUID) -> int:
    row = conn.execute(
        "UPDATE projects SET next_version_no = next_version_no + 1 WHERE id = %s "
        "RETURNING next_version_no - 1",
        (project_id,),
    ).fetchone()
    return int(row[0])


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
               support, supersedes_event_id, partition, similar_rejected_event_no
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
            similar_rejected_event_no=row[11],
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


@dataclass(frozen=True)
class _StoredReport:
    """``_store_report`` の結果（設計書 §5.3 (12) の通知の判定に使う）。"""

    report_id: UUID
    had_new_events: bool
    summary_for_mail: str


def _report_mode(
    conn: psycopg.Connection, project_id: UUID, audience: Audience
) -> Literal["diff", "baseline"]:
    existing = conn.execute(
        "SELECT 1 FROM reports WHERE project_id = %s AND audience = %s LIMIT 1",
        (project_id, audience),
    ).fetchone()
    return "baseline" if existing is None else "diff"


@dataclass(frozen=True)
class _BuiltReport:
    """保存する前のレポートの中身（``_build_report`` の結果）。"""

    mode: Literal["diff", "baseline"]
    judge_status: str
    body_markdown: str
    mermaid_dsl: str | None
    evidence_catalog: dict
    event_nos: list[int]
    cited_labels: list[str]
    input_segment_ids: list[str]
    flags: dict[str, object]
    summary_for_mail: str
    had_new_events: bool


def _build_report(
    *,
    project_id: UUID,
    mode: Literal["diff", "baseline"],
    goal_description: str,
    audience: Audience,
    active_events: list[_EventRow],
    all_events: list[_EventRow],
    state_map: dict[str, tuple[bool, SourceVisibility]],
    new_event_nos: set[int],
    llm_fn,
    suspected_injection_labels: set[str],
    allow_regeneration: bool,
) -> _BuiltReport:
    """レポートの中身を作る（LLM を呼ぶ）。DB には触れない。"""

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
        allow_regeneration=allow_regeneration,
        unresolved_reason_event_nos=frozenset(
            e.event_no
            for e in active_events
            if e.kind in ("decision", "rejected_option") and e.reason is None
        ),
    )

    # AD-10：この版で警告してよい「過去に却下した案との類似」（その却下案の実効の
    # 可視性が、この版（audience）で見えるものだけ）。見えなければ警告を付けない
    # （全員向けの版で管理者限定の却下案との類似を示唆しない）。
    rejected_flags, rejected_by_event_no, rejected_evidence_labels = _rejected_similarity_for(
        active_events, all_events, state_map, audience
    )

    render_events = [
        RenderEvent(
            event_no=e.event_no,
            segment_ids=e.segment_ids,
            origin=e.origin,
            kind=e.kind,
            reason=e.reason,
            similar_rejected_event_no=rejected_by_event_no.get(e.event_no),
        )
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

    input_segment_ids = sorted(
        {sid for e in active_events for sid in e.segment_ids} | rejected_evidence_labels
    )
    cited_labels = sorted({sid for labels in rendered.evidence_catalog.values() for sid in labels})

    flags: dict[str, object] = {
        # 誘導の文は決定の根拠にならないので、根拠の区切り（input_segment_ids）で絞ると
        # 必ず落ちる。呼び出し側が組ごとに渡す番号（全員向けの版には全員向けの組の番号
        # だけ）をそのまま使う（I1 は呼び出し側の組の分け方で守られる）。
        "suspected_injection": sorted(suspected_injection_labels),
        "unverified_ai_count": rendered.unverified_ai_count,
        "rejected_similarity": rejected_flags,
    }

    return _BuiltReport(
        mode=mode,
        judge_status=result.judge_status,
        body_markdown=rendered.body_markdown,
        mermaid_dsl=mermaid_dsl,
        evidence_catalog=rendered.evidence_catalog,
        event_nos=[e.event_no for e in active_events],
        cited_labels=cited_labels,
        input_segment_ids=input_segment_ids,
        flags=flags,
        summary_for_mail=result.output.summary_for_mail,
        had_new_events=bool(audience_new_event_nos),
    )


def _rejected_similarity_for(
    active_events: list[_EventRow],
    all_events: list[_EventRow],
    state_map: dict[str, tuple[bool, SourceVisibility]],
    audience: Audience,
) -> tuple[list[dict[str, int]], dict[int, int], set[str]]:
    """AD-10：この版（``audience``）で見せてよい「過去に却下した案との類似」を選ぶ。

    却下案そのもの（``similar_rejected_event_no`` が指す出来事）の実効の可視性を、
    この版の audience の規則で判定し、見えるものだけを残す。戻り値は
    (``flags.rejected_similarity`` そのままの形のリスト、event_no → rejected_event_no
    の対応、却下案の根拠の区切り（input_segment_ids に足す分）)。
    """

    events_by_no = {e.event_no: e for e in all_events}
    flags: list[dict[str, int]] = []
    by_event_no: dict[int, int] = {}
    evidence_labels: set[str] = set()

    for event in active_events:
        if event.similar_rejected_event_no is None:
            continue
        target = events_by_no.get(event.similar_rejected_event_no)
        if target is None:
            continue
        eff = visibility.effective_visibility(
            partition=target.partition, source_states=_states_for(target.segment_ids, state_map)
        )
        if eff == "excluded":
            continue
        if audience == "all" and eff != "all":
            continue
        flags.append({"event_no": event.event_no, "rejected_event_no": target.event_no})
        by_event_no[event.event_no] = target.event_no
        evidence_labels.update(target.segment_ids)

    return flags, by_event_no, evidence_labels


def _store_report(
    conn: psycopg.Connection,
    built: _BuiltReport,
    *,
    project_id: UUID,
    run_id: UUID,
    audience: Audience,
    version_no: int,
) -> _StoredReport:
    """できた中身を保存する（呼び出し側のトランザクションの中で。LLM は呼ばない）。"""

    row = conn.execute(
        """
        INSERT INTO reports
            (project_id, run_id, version_no, audience, kind, judge_status, body_markdown,
             mermaid_dsl, evidence_catalog, event_ids, cited_segment_ids, input_segment_ids,
             flags, summary_for_mail)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            project_id,
            run_id,
            version_no,
            audience,
            built.mode,
            built.judge_status,
            built.body_markdown,
            built.mermaid_dsl,
            _to_jsonb(built.evidence_catalog),
            built.event_nos,
            built.cited_labels,
            built.input_segment_ids,
            _to_jsonb(built.flags),
            built.summary_for_mail,
        ),
    ).fetchone()

    return _StoredReport(
        report_id=row[0],
        had_new_events=built.had_new_events,
        summary_for_mail=built.summary_for_mail,
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
