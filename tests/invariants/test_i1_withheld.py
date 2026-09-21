"""I1・I5：脚注の無い出来事の根拠が非公開・除外になったときの withheld（§5.5、【C-2】）。

版は追記専用で書き換えないので、表示時に ``input_segment_ids``（脚注に引用され
ていない出来事の根拠も含む）のソースの今の状態をもう一度確かめる。member には
可視性が ``managers_only`` に変わっただけで withheld になり、manager には除外に
なったときだけ withheld になる。
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

_FAKE_SUPPORT = json.dumps({"results": ["supported", "supported"]})
_FAKE_EXTRACT = json.dumps(
    {
        "events": [
            {
                "kind": "decision",
                "summary": "決定A",
                "reason": None,
                "occurred_at": "2026-09-21T09:00:00+09:00",
                "segment_ids": ["S1-1"],
                "origin": "human_originated",
                "supersedes_event_no": None,
                "conflicts_with_event_no": None,
            },
            {
                "kind": "finding",
                "summary": "決定Aの背景としてわかったこと",
                "reason": None,
                "occurred_at": "2026-09-21T09:05:00+09:00",
                "segment_ids": ["S2-1"],
                "origin": "human_originated",
                "supersedes_event_no": None,
                "conflicts_with_event_no": None,
            },
        ],
        "suspected_injection_segment_ids": [],
    }
)
# 決定A（event_no=1）だけを引用し、finding（event_no=2）は脚注が付かない
# （それでも input_segment_ids には両方の根拠が入る）。
_FAKE_ASSEMBLE = json.dumps(
    {
        "sections": {
            "purpose": [{"text": "目的の要約", "event_nos": [1]}],
            "current_state": [],
            "direction": [],
        },
        "summary_for_mail": "要約",
    }
)


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    text = {
        "EXTRACT": _FAKE_EXTRACT,
        "SUPPORT": _FAKE_SUPPORT,
        "ASSEMBLE": _FAKE_ASSEMBLE,
    }.get(stage, json.dumps({"status": "pass", "notes": []}))
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_project_with_two_sources(migrated_database_url: str) -> tuple[UUID, UUID, UUID, UUID]:
    """引用される決定Aの根拠（source1）と、引用されない finding の根拠（source2）。"""

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

        source1 = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        v1 = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source1,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s", (v1, source1)
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, v1, "決定Aの根拠になる発言"),
        )

        source2 = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 2, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, member_id),
        ).fetchone()[0]
        v2 = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source2,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s", (v2, source2)
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S2-1', 1, 'user', %s, true, false)",
            (project_id, v2, "決定Aの背景の発言（脚注は付かない）"),
        )
        conn.commit()
    return project_id, manager_id, member_id, source2


def _run(migrated_database_url: str, worker_settings, project_id: UUID) -> int:
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
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )
    assert outcome.outcome == "report_created"
    return outcome.version_no


def test_member_sees_withheld_when_uncited_sources_source_becomes_managers_only(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers: AuthHeaders
) -> None:
    project_id, manager_id, member_id, uncited_source_id = _setup_project_with_two_sources(
        migrated_database_url
    )
    version_no = _run(migrated_database_url, worker_settings, project_id)

    # 実行前は withheld ではない。
    before = api_client.get(
        f"/internal/projects/{project_id}/reports/{version_no}", headers=auth_headers(member_id)
    )
    assert before.json()["withheld"] is False

    # 引用されていない finding の根拠（source2）だけを、後から managers_only にする。
    resp = api_client.patch(
        f"/internal/sources/{uncited_source_id}/visibility",
        headers=auth_headers(manager_id),
        json={"visibility": "managers_only", "reason": "confidential"},
    )
    assert resp.status_code == 200

    member_after = api_client.get(
        f"/internal/projects/{project_id}/reports/{version_no}", headers=auth_headers(member_id)
    )
    assert member_after.status_code == 200
    assert member_after.json()["withheld"] is True

    # manager には managers_only は withheld の理由にならない。
    manager_after = api_client.get(
        f"/internal/projects/{project_id}/reports/{version_no}", headers=auth_headers(manager_id)
    )
    assert manager_after.status_code == 200
    assert manager_after.json()["withheld"] is False


def test_manager_also_sees_withheld_when_uncited_sources_source_is_excluded(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers: AuthHeaders
) -> None:
    project_id, manager_id, _member_id, uncited_source_id = _setup_project_with_two_sources(
        migrated_database_url
    )
    version_no = _run(migrated_database_url, worker_settings, project_id)

    resp = api_client.patch(
        f"/internal/sources/{uncited_source_id}/exclusion",
        headers=auth_headers(manager_id),
        json={"is_excluded": True, "reason": "irrelevant"},
    )
    assert resp.status_code == 200

    manager_after = api_client.get(
        f"/internal/projects/{project_id}/reports/{version_no}", headers=auth_headers(manager_id)
    )
    assert manager_after.status_code == 200
    assert manager_after.json()["withheld"] is True
