"""I1：管理者限定の内容がメンバー向けに出ない（設計書 §5.3 (2)、【C-1】ほか）。

偽の LLM が受け取った全メッセージを記録し、``all`` の組の呼び出しに管理者限定の
区切りの文字列が1つも含まれないことを確かめる。member の W19 の応答にも
管理者限定の文字列が無いことを確かめる。
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

_MANAGERS_ONLY_SECRET_STRING = "極秘のレイオフ計画XYZ123"
_ALL_VISIBLE_STRING = "配信基盤の相談ABC789"


def _extract_response_for(has_secret: bool) -> str:
    if has_secret:
        return json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "レイオフ計画を承認",
                        "reason": None,
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S1-2"],
                        "origin": "human_originated",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    return json.dumps(
        {
            "events": [
                {
                    "kind": "decision",
                    "summary": "配信基盤はResendを採用",
                    "reason": None,
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
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def make_recording_fake_llm(calls: list[tuple[str, list[dict]]]):
    def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        calls.append((stage, messages))
        all_text = json.dumps(messages, ensure_ascii=False)
        if stage == "EXTRACT":
            has_secret = _MANAGERS_ONLY_SECRET_STRING in all_text
            text = _extract_response_for(has_secret)
        elif stage == "SUPPORT":
            text = _FAKE_SUPPORT
        elif stage == "ASSEMBLE":
            text = json.dumps(
                {
                    "sections": {
                        "purpose": [{"text": "全体の要約", "event_nos": [1]}],
                        "current_state": [],
                        "direction": [],
                    },
                    "summary_for_mail": "要約",
                }
            )
        else:
            text = _FAKE_JUDGE
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=10,
            output_tokens=10,
            model="fake",
            estimated=True,
        )

    return _fake_llm


def _setup_project(migrated_database_url: str) -> tuple[UUID, UUID, UUID]:
    """all 用の区切りと managers_only 用の区切りを両方持つプロジェクトを作る。"""

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

        # all のソース
        source_all = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        version_all = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_all,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_all, source_all),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, version_all, _ALL_VISIBLE_STRING),
        )

        # managers_only のソース
        source_mgr = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 2, 'conversation', %s, now(), "
            "'managers_only', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        version_mgr = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_mgr,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_mgr, source_mgr),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S2-1', 1, 'user', %s, true, false)",
            (project_id, version_mgr, _MANAGERS_ONLY_SECRET_STRING),
        )
        conn.commit()
    return project_id, manager_id, member_id


def test_all_partition_llm_calls_never_see_managers_only_text(
    migrated_database_url: str, worker_settings
) -> None:
    project_id, _manager_id, _member_id = _setup_project(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    with psycopg.connect(migrated_database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    calls: list[tuple[str, list[dict]]] = []
    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=make_recording_fake_llm(calls),
    )
    assert outcome.outcome == "report_created"

    extract_calls = [(stage, messages) for stage, messages in calls if stage == "EXTRACT"]
    assert len(extract_calls) == 2  # all の組・managers の組で1回ずつ

    all_partition_calls_without_secret = [
        messages
        for _stage, messages in extract_calls
        if _ALL_VISIBLE_STRING in json.dumps(messages, ensure_ascii=False)
        and _MANAGERS_ONLY_SECRET_STRING not in json.dumps(messages, ensure_ascii=False)
    ]
    assert len(all_partition_calls_without_secret) == 1, (
        "all の組の呼び出しに managers_only の文字列が1つも含まれてはいけない"
    )


def test_member_view_never_contains_managers_only_text(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers: AuthHeaders
) -> None:
    project_id, manager_id, member_id = _setup_project(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    with psycopg.connect(migrated_database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    calls: list[tuple[str, list[dict]]] = []
    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=make_recording_fake_llm(calls),
    )
    assert outcome.outcome == "report_created"

    response = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        headers=auth_headers(member_id),
    )
    assert response.status_code == 200
    body_text = json.dumps(response.json(), ensure_ascii=False)
    assert _MANAGERS_ONLY_SECRET_STRING not in body_text

    # manager には(managers_only の出来事があれば)別の版が見える。
    manager_response = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        headers=auth_headers(manager_id),
    )
    assert manager_response.status_code == 200
