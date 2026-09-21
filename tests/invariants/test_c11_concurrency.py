"""C11：同時実行してもレポートが1版しかできない（設計書 §5.2）。

2本に分ける：
1. 同じ run_id の二重送信（片方が ``report_created``、もう片方は既に走った run の
   状態をそのまま返すだけ）。
2. 同じプロジェクトの別々の run_id の同時実行（片方が ``report_created``、もう
   片方はアドバイザリロックが取れず ``locked`` で ``status='done'``）。

どちらも、``run.execute_run`` の「まず run を引き受けてからロックを取る」順序
（コーディネーターからの指摘、run.py 参照）が正しく効いているかを、バリアで
2スレッドの開始を揃えたうえで20回繰り返して確かめる。
"""

import json
import threading
import time
from uuid import UUID

import psycopg
import pytest

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

_REPEATS = 20

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
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
_FAKE_ASSEMBLE = json.dumps(
    {
        "sections": {
            "purpose": [{"text": "目的はG", "event_nos": [1]}],
            "current_state": [],
            "direction": [],
        },
        "summary_for_mail": "要約",
    }
)
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def _slow_fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    # 実行時間を延ばし、2スレッドの実行区間が確実に重なるようにする。
    time.sleep(0.2)
    text = {
        "EXTRACT": _FAKE_EXTRACT,
        "SUPPORT": _FAKE_SUPPORT,
        "ASSEMBLE": _FAKE_ASSEMBLE,
        "JUDGE": _FAKE_JUDGE,
    }[stage]
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_project(database_url: str) -> UUID:
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
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', 'Xを採用する', "
            "true, false)",
            (project_id, version_id),
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


@pytest.mark.parametrize("attempt", range(_REPEATS))
def test_same_run_id_double_submit_is_not_run_twice(
    attempt: int, migrated_database_url: str, worker_settings
) -> None:
    """同じ run_id を2スレッドから同時に送っても、片方だけが実際に処理する。"""

    project_id = _setup_project(migrated_database_url)
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def _worker() -> None:
        barrier.wait()
        outcome = run_module.execute_run(
            run_id, worker_settings=worker_settings, pool=pool, llm_call=_slow_fake_llm
        )
        outcomes.append(outcome.outcome)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 片方が実際に処理する（report_created）。もう片方は run を引き受けられず
    # （queued → running の更新に失敗）、run.py::_stored_outcome で「今の状態」を
    # そのまま返すだけ：勝った側がまだ終わっていなければ outcome 列は NULL のまま
    # なので "locked" を返す（何も書き込まない、安全側の既定値）。ここで確かめたい
    # のは、負けた側が「勝った側の run を locked で閉じてしまい、勝った側も
    # locked を返す」という事故（修正前のバグ）が起きないこと。
    assert sorted(outcomes) == ["locked", "report_created"]

    with psycopg.connect(migrated_database_url) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
    assert count == 1


@pytest.mark.parametrize("attempt", range(_REPEATS))
def test_two_different_runs_same_project_only_one_executes(
    attempt: int, migrated_database_url: str, worker_settings
) -> None:
    """同じプロジェクトの別々の run_id を同時に実行しても、ロックで直列化される。"""

    project_id = _setup_project(migrated_database_url)
    run_id_a = _insert_queued_run(migrated_database_url, project_id)
    run_id_b = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    barrier = threading.Barrier(2)
    outcomes: dict[UUID, str] = {}

    def _worker(run_id: UUID) -> None:
        barrier.wait()
        outcome = run_module.execute_run(
            run_id, worker_settings=worker_settings, pool=pool, llm_call=_slow_fake_llm
        )
        outcomes[run_id] = outcome.outcome

    threads = [
        threading.Thread(target=_worker, args=(run_id_a,)),
        threading.Thread(target=_worker, args=(run_id_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes.values()) == ["locked", "report_created"]

    locked_run_id = run_id_a if outcomes[run_id_a] == "locked" else run_id_b
    with psycopg.connect(migrated_database_url) as conn:
        status, db_outcome = conn.execute(
            "SELECT status, outcome FROM runs WHERE id = %s", (locked_run_id,)
        ).fetchone()
        report_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
    assert (status, db_outcome) == ("done", "locked")
    assert report_count == 1
