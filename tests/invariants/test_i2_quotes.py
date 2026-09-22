"""I2：引用は原文と一字一句一致する（設計書 §5.6、【C-X5】）。

LLM の出力にわざと違う引用文を入れても、W19 の ``footnotes``・``node_evidence`` の
すべての ``text`` が、それぞれの ``label`` で DB から引いた原文と完全一致する
こと。図のラベル（``mermaid_dsl``）には原文を載せず、出来事の要約だけを載せる
ことも確かめる。
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

_ORIGINAL_SEGMENT_TEXT = "予算はResendにする。安いから決めた。"
# LLM は根拠の番号だけを返す設計だが、万一違う引用文を書いてきても、
# 表示のたびに DB の原文から引き直すため、footnotes には絶対に混ざらない。
_FAKE_EXTRACT = json.dumps(
    {
        "events": [
            {
                "kind": "decision",
                "summary": (
                    "配信基盤はResendを採用（これはLLMがでっち上げた引用「本当は別の内容」）"
                ),
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
            "purpose": [{"text": "Resendを採用した", "event_nos": [1]}],
            "current_state": [],
            "direction": [],
        },
        "summary_for_mail": "要約",
    }
)
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    text = {
        "EXTRACT": _FAKE_EXTRACT,
        "SUPPORT": _FAKE_SUPPORT,
        "ASSEMBLE": _FAKE_ASSEMBLE,
        "JUDGE": _FAKE_JUDGE,
    }[stage]
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_and_run(migrated_database_url: str, worker_settings) -> tuple[UUID, UUID, int]:
    with psycopg.connect(migrated_database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) "
            "VALUES (%s, %s, 'm@example.test', 'manager')",
            (project_id, manager_id),
        )
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
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
            (project_id, version_id, _ORIGINAL_SEGMENT_TEXT),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )
    assert outcome.outcome == "report_created"
    return project_id, manager_id, outcome.version_no


def test_footnotes_and_node_evidence_match_db_verbatim_despite_fake_llm_quotes(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers: AuthHeaders
) -> None:
    project_id, manager_id, version_no = _setup_and_run(migrated_database_url, worker_settings)

    response = api_client.get(
        f"/internal/projects/{project_id}/reports/{version_no}",
        headers=auth_headers(manager_id),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["withheld"] is False

    all_texts = []
    for footnote in body["footnotes"].values():
        all_texts.extend(item["text"] for item in footnote)
    for node in body["node_evidence"].values():
        all_texts.extend(item["text"] for item in node)

    assert all_texts, "footnotes・node_evidence が空だとこのテストの意味が無い"
    for text in all_texts:
        assert text == _ORIGINAL_SEGMENT_TEXT

    # 図のラベルには原文を載せず、要約だけを載せる。
    assert _ORIGINAL_SEGMENT_TEXT not in body["mermaid_dsl"]
    assert "配信基盤はResendを採用" in body["mermaid_dsl"]
