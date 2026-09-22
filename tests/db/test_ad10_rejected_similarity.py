"""AD-10：却下案の再浮上の検知（設計書 §5.3、不変条件 I1）。

偽の LLM が ``similar_rejected_event_no`` を返すと、番号の検査を通り、W19 に
``flags.rejected_similarity`` と却下案の ``node_evidence``、本文にマーカー、
冒頭に件数が出ること。渡していない番号・管理者限定の却下案の番号を返しても、
全員向けの版には出ないこと（member の W19 に管理者限定の却下案の文字列が無い）。
"""

import json
from uuid import UUID

import psycopg
from fastapi.testclient import TestClient

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
_FAKE_JUDGE_PASS = json.dumps({"status": "pass", "notes": []})
_REJECTED_TEXT = "Yを採用しないことにした（コストが高い）"


def _fake_extract(events: list[dict]) -> str:
    return json.dumps({"events": events, "suspected_injection_segment_ids": []})


def _fake_assemble(event_nos: list[int]) -> str:
    """初回の実行なのでベースラインモードになる（見出しが purpose/current_state/
    direction）。差分モードの見出しも一緒に含めておき、どちらのモードでも同じ文が
    載るようにする（``assemble_report`` は使う見出しのキーだけを拾う）。"""

    sentence = {"text": "Yを再度採用することにした", "event_nos": event_nos}
    sections = {
        "decisions": [sentence],
        "reasons": [],
        "rejected_options": [],
        "next_steps": [],
        "current_status": [],
        "purpose": [sentence],
        "current_state": [],
        "direction": [],
    }
    return json.dumps({"sections": sections, "summary_for_mail": "要約"})


def _setup_project_with_prior_rejection(
    database_url: str, *, rejected_visibility: str = "all"
) -> tuple[UUID, UUID, UUID, int]:
    """manager・member・以前却下した案（event_no=1）・新しい未消費の区切りを用意する。"""

    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description, next_event_no) "
            "VALUES ('P', 'G', 2) RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        member_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) VALUES "
            "(%s, %s, 'mgr@example.test', 'manager'), (%s, %s, 'mem@example.test', 'member')",
            (project_id, manager_id, project_id, member_id),
        )

        # events.run_id の外部キーを満たすためのダミー run（完了扱いで先に作る）。
        old_run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'done') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]

        # 却下案の根拠（旧いソース、既に消費済みでよい）。
        rejected_source = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "%s, 2) RETURNING id",
            (project_id, manager_id, rejected_visibility),
        ).fetchone()[0]
        rejected_version = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (rejected_source,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (rejected_version, rejected_source),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, true)",
            (project_id, rejected_version, _REJECTED_TEXT),
        )
        rejected_partition = "all" if rejected_visibility == "all" else "managers"
        conn.execute(
            "INSERT INTO events (project_id, run_id, event_no, kind, summary, reason, "
            "occurred_at, segment_ids, visibility_at_creation, support, partition) "
            "VALUES (%s, %s, 1, 'rejected_option', 'Yは却下', 'コストが高い', now(), "
            "'{S1-1}', %s, 'supported', %s)",
            (project_id, old_run_id, rejected_visibility, rejected_partition),
        )

        # 新しい（未消費の）区切り（次の実行の抽出の対象）。
        new_source = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 2, 'conversation', %s, now(), "
            "'all', 2) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        new_version = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (new_source,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (new_version, new_source),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S2-1', 1, 'user', %s, true, false)",
            (project_id, new_version, "やっぱりYを採用することにした"),
        )
        conn.commit()
    return project_id, manager_id, member_id, 1  # 1 = 事前に作った却下案の event_no


def _insert_queued_run(database_url: str, project_id: UUID) -> UUID:
    with psycopg.connect(database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    return run_id


def test_similar_rejected_event_no_surfaces_in_w19(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers
) -> None:
    project_id, manager_id, member_id, rejected_no = _setup_project_with_prior_rejection(
        migrated_database_url
    )
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    extract_messages_seen: list[str] = []

    def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        if stage == "EXTRACT":
            extract_messages_seen.append(json.dumps(messages, ensure_ascii=False))
            text = _fake_extract(
                [
                    {
                        "kind": "decision",
                        "summary": "Yを再度採用",
                        "reason": "コストが下がったから",
                        "occurred_at": "2026-09-21T11:00:00+09:00",
                        "segment_ids": ["S2-1"],
                        "origin": "human_originated",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                        "similar_rejected_event_no": rejected_no,
                    }
                ]
            )
        elif stage == "SUPPORT":
            text = _FAKE_SUPPORT
        elif stage == "ASSEMBLE":
            text = _fake_assemble([2])
        else:
            text = _FAKE_JUDGE_PASS
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=10,
            output_tokens=10,
            model="fake",
            estimated=True,
        )

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )
    assert outcome.outcome == "report_created"

    # 過去の却下案が抽出の入力に渡されている（AD-10）。
    assert any("Yは却下" in text for text in extract_messages_seen)

    with psycopg.connect(migrated_database_url) as conn:
        stored = conn.execute(
            "SELECT similar_rejected_event_no FROM events WHERE project_id = %s AND event_no = 2",
            (project_id,),
        ).fetchone()
    assert stored == (rejected_no,)

    resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        headers=auth_headers(manager_id),
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["flags"]["rejected_similarity"] == [
        {"event_no": 2, "rejected_event_no": rejected_no}
    ]
    assert f"〔過去に却下した案と類似：E{rejected_no}〕" in body["body_markdown"]
    assert "過去に却下した案と類似の可能性：1件" in body["body_markdown"]
    assert body["node_evidence"][f"E{rejected_no}"] == [{"label": "S1-1", "text": _REJECTED_TEXT}]


def test_manager_only_rejection_is_not_leaked_to_member(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers
) -> None:
    project_id, manager_id, member_id, rejected_no = _setup_project_with_prior_rejection(
        migrated_database_url, rejected_visibility="managers_only"
    )
    run_id = _insert_queued_run(migrated_database_url, project_id)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        if stage == "EXTRACT":
            text = _fake_extract(
                [
                    {
                        "kind": "decision",
                        "summary": "全員向けの新しい決定",
                        "reason": "理由",
                        "occurred_at": "2026-09-21T11:00:00+09:00",
                        "segment_ids": ["S2-1"],
                        "origin": "human_originated",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                        # 偽の LLM が、渡されていないはずの管理者限定の却下案の番号を
                        # 返した想定。全員向けの組の呼び出しにはこの番号は渡していない
                        # ので、コードの検査で null になるはず（I1）。
                        "similar_rejected_event_no": rejected_no,
                    }
                ]
            )
        elif stage == "SUPPORT":
            text = _FAKE_SUPPORT
        elif stage == "ASSEMBLE":
            text = _fake_assemble([2])
        else:
            text = _FAKE_JUDGE_PASS
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=10,
            output_tokens=10,
            model="fake",
            estimated=True,
        )

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )
    assert outcome.outcome == "report_created"

    with psycopg.connect(migrated_database_url) as conn:
        stored = conn.execute(
            "SELECT similar_rejected_event_no FROM events WHERE project_id = %s AND event_no = 2",
            (project_id,),
        ).fetchone()
    # 全員向けの組の抽出には管理者限定の却下案の番号は渡されていないため null になる。
    assert stored == (None,)

    member_resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        headers=auth_headers(member_id),
    )
    assert member_resp.status_code == 200
    member_body = member_resp.json()
    assert member_body["flags"].get("rejected_similarity", []) == []
    member_text = json.dumps(member_body, ensure_ascii=False)
    assert "Yは却下" not in member_text
    assert _REJECTED_TEXT not in member_text
