"""I1：``managers`` の組で作った出来事の実効の可視性の下限は ``managers_only``

（設計書 §5.5、【B-X1】）。根拠のソースを後から ``all`` にしても、その出来事は
全員向けの版には入らない。そのソースの区切りは、次の実行で全員向けの組の抽出に
回される（W11 のトランザクションで ``consumed``・``carry_count`` を戻す）。
"""

import json
from collections.abc import Callable
from uuid import UUID

import psycopg
from fastapi.testclient import TestClient

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult
from ai_hackathon_team_a.visibility import effective_visibility

AuthHeaders = Callable[..., dict[str, str]]

_ORIGINAL_SUMMARY = "経費削減案を承認（管理者限定の組で抽出）"
_REEXTRACTED_SUMMARY = "経費削減の方針を共有（全員向けの組で再抽出）"
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def _fake_llm_run1(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    if stage == "EXTRACT":
        text = json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": _ORIGINAL_SUMMARY,
                        "reason": None,
                        "occurred_at": "2026-09-21T09:00:00+09:00",
                        "segment_ids": ["S1-1"],
                        "origin": "human_originated",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    elif stage == "SUPPORT":
        text = _FAKE_SUPPORT
    elif stage == "ASSEMBLE":
        text = json.dumps(
            {
                "sections": {
                    "purpose": [{"text": "決定の要約", "event_nos": [1]}],
                    "current_state": [],
                    "direction": [],
                },
                "summary_for_mail": "要約1",
            }
        )
    else:
        text = _FAKE_JUDGE
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _fake_llm_run2(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    if stage == "EXTRACT":
        # run2 では S1-1 が all の組の入力として再度渡される（W11 で巻き戻したため）。
        text = json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": _REEXTRACTED_SUMMARY,
                        "reason": None,
                        "occurred_at": "2026-09-21T12:00:00+09:00",
                        "segment_ids": ["S1-1"],
                        "origin": "human_originated",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    elif stage == "SUPPORT":
        text = _FAKE_SUPPORT
    elif stage == "ASSEMBLE":
        text = json.dumps(
            {
                "sections": {
                    "decisions": [{"text": "共有した", "event_nos": [2]}],
                    "reasons": [],
                    "rejected_options": [],
                    "next_steps": [],
                    "current_status": [],
                },
                "summary_for_mail": "要約2",
            }
        )
    else:
        text = _FAKE_JUDGE
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_project_with_managers_only_source(
    migrated_database_url: str,
) -> tuple[UUID, UUID, UUID]:
    with psycopg.connect(migrated_database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        member_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) VALUES "
            "(%s, %s, 'mgr@example.test', 'manager'), (%s, %s, 'mem@example.test', 'member')",
            (project_id, manager_id, project_id, member_id),
        )

        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'managers_only', 1) RETURNING id",
            (project_id, manager_id),
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
            (project_id, version_id, "経費削減について管理職だけで話した"),
        )
        conn.commit()
    return project_id, manager_id, source_id


def _run(migrated_database_url: str, worker_settings, project_id: UUID, llm_call) -> int:
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    with psycopg.connect(migrated_database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=llm_call
    )
    assert outcome.outcome == "report_created"
    return outcome.version_no


def test_managers_partition_event_floor_and_reextraction_after_making_source_all(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers: AuthHeaders
) -> None:
    project_id, manager_id, source_id = _setup_project_with_managers_only_source(
        migrated_database_url
    )
    version1 = _run(migrated_database_url, worker_settings, project_id, _fake_llm_run1)
    assert version1 == 1

    # W11：managers_only → all（manager だけができる）。
    resp = api_client.patch(
        f"/internal/sources/{source_id}/visibility",
        headers=auth_headers(manager_id),
        json={"visibility": "all", "reason": "other"},
    )
    assert resp.status_code == 200

    with psycopg.connect(migrated_database_url) as conn:
        # 【B-X1】：ソースの可視性を all にしても、managers の組で作った出来事の
        # 実効の可視性は managers_only のまま（下限）。
        partition = conn.execute(
            "SELECT partition FROM events WHERE project_id = %s AND summary = %s",
            (project_id, _ORIGINAL_SUMMARY),
        ).fetchone()[0]
        source_visibility, source_is_excluded = conn.execute(
            "SELECT visibility, is_excluded FROM project_sources WHERE id = %s", (source_id,)
        ).fetchone()
        assert source_visibility == "all"  # W11 が確かに変えている
        source_states = [(source_is_excluded, source_visibility)]
        assert effective_visibility(partition=partition, source_states=source_states) == (
            "managers_only"
        )

        # 巻き戻されて、次の実行で全員向けの組の入力に戻る。
        seg = conn.execute(
            "SELECT consumed, carry_count FROM source_segments WHERE label = 'S1-1'"
        ).fetchone()
        assert seg == (False, 0)

    version2 = _run(migrated_database_url, worker_settings, project_id, _fake_llm_run2)
    assert version2 == 2

    with psycopg.connect(migrated_database_url) as conn:
        reextracted = conn.execute(
            "SELECT partition FROM events WHERE project_id = %s AND summary = %s",
            (project_id, _REEXTRACTED_SUMMARY),
        ).fetchone()
    assert reextracted is not None
    assert reextracted[0] == "all"  # 全員向けの組で再抽出された新しい出来事
