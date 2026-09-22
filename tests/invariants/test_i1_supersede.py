"""I1：管理者限定の出来事が全員向けの決定を置き換えても、全員向けの版からは

消えないこと（設計書 §5.3 (2)、【C-5】）。「今も有効」の判定は、置き換えた側が
その閲覧単位（組・版）で見えるときだけ「置き換え」とみなす。
"""

import json
from collections.abc import Callable
from uuid import UUID

import psycopg
from fastapi.testclient import TestClient

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

AuthHeaders = Callable[..., dict[str, str]]

_ORIGINAL_SUMMARY = "配信基盤はResendを採用"
_SUPERSEDING_SUMMARY = "配信基盤は非公開の事情でSendGridに変更"

_FAKE_EXTRACT_RUN1 = json.dumps(
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
_FAKE_ASSEMBLE_RUN1 = json.dumps(
    {
        "sections": {
            "purpose": [{"text": "配信基盤を決めた", "event_nos": [1]}],
            "current_state": [],
            "direction": [],
        },
        "summary_for_mail": "要約1",
    }
)
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def _fake_llm_run1(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    text = {
        "EXTRACT": _FAKE_EXTRACT_RUN1,
        "SUPPORT": _FAKE_SUPPORT,
        "ASSEMBLE": _FAKE_ASSEMBLE_RUN1,
    }.get(stage, _FAKE_JUDGE)
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _fake_llm_run2(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    all_text = json.dumps(messages, ensure_ascii=False)
    if stage == "EXTRACT":
        if "S2-1" in all_text:  # managers_only の区切りを渡された呼び出し
            text = json.dumps(
                {
                    "events": [
                        {
                            "kind": "decision",
                            "summary": _SUPERSEDING_SUMMARY,
                            "reason": "非公開の事情",
                            "occurred_at": "2026-09-21T11:00:00+09:00",
                            "segment_ids": ["S2-1"],
                            "origin": "human_originated",
                            "supersedes_event_no": 1,
                            "conflicts_with_event_no": None,
                        }
                    ],
                    "suspected_injection_segment_ids": [],
                }
            )
        else:
            text = json.dumps({"events": [], "suspected_injection_segment_ids": []})
    elif stage == "SUPPORT":
        text = _FAKE_SUPPORT
    elif stage == "ASSEMBLE":
        has_secret = _SUPERSEDING_SUMMARY in all_text
        event_nos = [2] if has_secret else [1]
        text = json.dumps(
            {
                "sections": {
                    "decisions": [{"text": "決定の更新", "event_nos": event_nos}],
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


def _setup_project(migrated_database_url: str) -> tuple[UUID, UUID, UUID]:
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

        source_all = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        v1 = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_all,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s", (v1, source_all)
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, v1, "配信基盤の相談"),
        )
        conn.commit()
    return project_id, manager_id, member_id


def _add_managers_only_segment(
    migrated_database_url: str, project_id: UUID, manager_id: UUID
) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        source_mgr = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 2, 'conversation', %s, now(), "
            "'managers_only', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        v2 = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_mgr,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s", (v2, source_mgr)
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S2-1', 1, 'user', %s, true, false)",
            (project_id, v2, "非公開の事情でSendGridに変更した"),
        )
        conn.commit()


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


def test_all_audience_report_keeps_original_decision_after_managers_only_supersede(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers: AuthHeaders
) -> None:
    project_id, manager_id, member_id = _setup_project(migrated_database_url)
    version1 = _run(migrated_database_url, worker_settings, project_id, _fake_llm_run1)
    assert version1 == 1

    _add_managers_only_segment(migrated_database_url, project_id, manager_id)
    version2 = _run(migrated_database_url, worker_settings, project_id, _fake_llm_run2)
    assert version2 == 2

    member_resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{version2}", headers=auth_headers(member_id)
    )
    assert member_resp.status_code == 200
    member_body = member_resp.json()
    assert member_body["audience"] == "all"
    assert member_body["withheld"] is False

    timeline_summaries = [item["summary"] for item in member_body["timeline"]]
    assert _ORIGINAL_SUMMARY in timeline_summaries
    assert _SUPERSEDING_SUMMARY not in timeline_summaries

    manager_resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{version2}", headers=auth_headers(manager_id)
    )
    assert manager_resp.status_code == 200
    manager_body = manager_resp.json()
    assert manager_body["audience"] == "managers"
    manager_timeline_summaries = [item["summary"] for item in manager_body["timeline"]]
    # manager から見ると、置き換えた側（管理者限定）が見えるので元の決定は退く。
    assert _SUPERSEDING_SUMMARY in manager_timeline_summaries
    assert _ORIGINAL_SUMMARY not in manager_timeline_summaries
