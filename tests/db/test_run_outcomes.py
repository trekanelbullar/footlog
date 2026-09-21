"""run の outcome（locked / no_new_events / report_created / cost_limited、設計書 §5.1〜§5.3）。"""

import functools
import json
from uuid import UUID

import psycopg

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.config import Settings
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult, call_llm

_FAKE_EXTRACT = json.dumps(
    {
        "events": [
            {
                "kind": "decision",
                "summary": "Xを採用",
                "reason": "安いから",
                "occurred_at": "2026-09-21T10:00:00+09:00",
                "segment_ids": ["S1-1"],
                "origin": "human_originated",
                "supersedes_event_no": None,
                "conflicts_with_event_no": None,
            }
        ],
        "suspected_injection_segment_ids": [],
    }
)
_FAKE_EMPTY_EXTRACT = json.dumps({"events": [], "suspected_injection_segment_ids": []})
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
_FAKE_ASSEMBLE = json.dumps(
    {
        "sections": {
            "purpose": [{"text": "目的はG", "event_nos": [1]}],
            "current_state": [{"text": "Xを採用した", "event_nos": [1]}],
            "direction": [],
        },
        "summary_for_mail": "要約",
    }
)
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def _fake_llm(extract_text: str = _FAKE_EXTRACT):
    def _call(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        text = {
            "EXTRACT": extract_text,
            "SUPPORT": _FAKE_SUPPORT,
            "ASSEMBLE": _FAKE_ASSEMBLE,
            "JUDGE": _FAKE_JUDGE,
        }[stage]
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=10,
            output_tokens=10,
            model="fake",
            estimated=True,
        )

    return _call


def _setup_project_with_segment(database_url: str, *, text: str = "Xを採用する") -> UUID:
    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        user_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) "
            "VALUES (%s, %s, 'm@example.test', 'manager')",
            (project_id, user_id),
        )
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, user_id),
        ).fetchone()[0]
        version_id = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_id, source_id),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, version_id, text),
        )
        conn.commit()
    return project_id


def _insert_queued_run(database_url: str, project_id: UUID) -> UUID:
    with psycopg.connect(database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    return run_id


def test_outcome_report_created(migrated_database_url: str, worker_settings) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm()
    )

    assert outcome.outcome == "report_created"
    assert outcome.version_no == 1
    with psycopg.connect(migrated_database_url) as conn:
        status, step, db_outcome = conn.execute(
            "SELECT status, step, outcome FROM runs WHERE id = %s", (run_id,)
        ).fetchone()
    assert (status, step, db_outcome) == ("done", "done", "report_created")


def test_outcome_no_new_events(migrated_database_url: str, worker_settings) -> None:
    project_id = _setup_project_with_segment(migrated_database_url, text="ただの雑談")
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_fake_llm(extract_text=_FAKE_EMPTY_EXTRACT),
    )

    assert outcome.outcome == "no_new_events"
    with psycopg.connect(migrated_database_url) as conn:
        status, db_outcome = conn.execute(
            "SELECT status, outcome FROM runs WHERE id = %s", (run_id,)
        ).fetchone()
        report_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
    assert (status, db_outcome) == ("done", "no_new_events")
    assert report_count == 0


def test_outcome_locked_when_advisory_lock_is_held(
    migrated_database_url: str, worker_settings
) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)

    lock_conn = psycopg.connect(migrated_database_url, autocommit=True)
    lock_conn.execute(
        "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"decision-trace:{project_id}",)
    )
    try:
        outcome = run_module.execute_run(
            run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm()
        )
    finally:
        lock_conn.execute(
            "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (f"decision-trace:{project_id}",)
        )
        lock_conn.close()

    assert outcome.outcome == "locked"
    with psycopg.connect(migrated_database_url) as conn:
        status, db_outcome = conn.execute(
            "SELECT status, outcome FROM runs WHERE id = %s", (run_id,)
        ).fetchone()
    # ロックが取れなかった実行は、出来事もレポートも書かない（design §5.2）。ただし run 自体は
    # done / locked で閉じる（queued のままだと W16 の問い合わせが終わらないため）。
    assert (status, db_outcome) == ("done", "locked")
    with psycopg.connect(migrated_database_url) as conn:
        assert (
            conn.execute("SELECT count(*) FROM reports WHERE run_id = %s", (run_id,)).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM events WHERE run_id = %s", (run_id,)).fetchone()[0]
            == 0
        )


class _NeverCalledOrcaClient:
    def complete(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("OrcaClient.complete must not be called when over budget")


def test_outcome_cost_limited(migrated_database_url: str, worker_settings) -> None:
    """上限超過は本物の ``call_llm`` の予約チェックで起こる（偽の llm_call では確かめられない）。"""

    project_id = _setup_project_with_segment(migrated_database_url)
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 0.0000001})
    fake_llm_call = functools.partial(
        call_llm,
        orca_client=_NeverCalledOrcaClient(),
        settings=Settings(_env_file=None, api_key="test-key"),
        worker_settings=worker_settings,
        pool=pool,
    )

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=fake_llm_call
    )

    assert outcome.outcome == "cost_limited"
    with psycopg.connect(migrated_database_url) as conn:
        status, db_outcome = conn.execute(
            "SELECT status, outcome FROM runs WHERE id = %s", (run_id,)
        ).fetchone()
    assert (status, db_outcome) == ("failed", "cost_limited")


def test_retrying_the_same_run_id_returns_the_stored_outcome(
    migrated_database_url: str, worker_settings
) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    first = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm()
    )
    second = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm()
    )

    assert first.outcome == "report_created"
    assert second.outcome == "report_created"
    assert second.version_no == first.version_no
    with psycopg.connect(migrated_database_url) as conn:
        report_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
    assert report_count == 1  # 二重送信で版が増えていない
