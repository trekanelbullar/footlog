"""I1（メール・質問関係）：設計書 §5.3 (7)(12)、【C-1】【B-X2】。

- 送るメール本文は全員向けの版の ``summary_for_mail`` から組み立てられ、管理者
  限定の文字列を含まない。
- 管理者向けの組の出来事では、根拠のソースの登録者に manager がいなければ
  質問が作られない。member が宛先になることはない【C-1】。
- 実行中に可視性が変わると、レポートは保存されるがメール・アプリ内通知は
  送られない【B-X2】。
"""

import json
from uuid import UUID

import httpx
import psycopg

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

_ALL_TEXT = "Xを採用することにした。理由は安いから。"
_MANAGERS_SECRET_TEXT = "役員報酬を20%上げることにした。理由は業績目標達成のため。"
_SECRET_MARKER = "役員報酬"


def _messages_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def _support_response(messages: list[dict]) -> str:
    count = _messages_text(messages).count("## 出来事")
    return json.dumps({"results": ["supported" for _ in range(max(count, 1))]})


def _extract_response(messages: list[dict]) -> str:
    text = _messages_text(messages)
    if _SECRET_MARKER in text:
        return json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "役員報酬を引き上げる",
                        "reason": "業績目標達成のため",
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S2-1"],
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


def _assemble_response(messages: list[dict]) -> str:
    text = _messages_text(messages)
    if _SECRET_MARKER in text:
        return json.dumps(
            {
                "sections": {
                    "purpose": [{"text": "目的はG", "event_nos": [1, 2]}],
                    "current_state": [{"text": "役員報酬を引き上げた", "event_nos": [2]}],
                    "direction": [],
                },
                "summary_for_mail": f"{_SECRET_MARKER}の件を含む更新です。",
            }
        )
    return json.dumps(
        {
            "sections": {
                "purpose": [{"text": "目的はG", "event_nos": [1]}],
                "current_state": [{"text": "Xを採用した", "event_nos": [1]}],
                "direction": [],
            },
            "summary_for_mail": "進捗のお知らせ：Xを採用しました。",
        }
    )


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    if stage == "EXTRACT":
        text = _extract_response(messages)
    elif stage == "SUPPORT":
        text = _support_response(messages)
    elif stage == "ASSEMBLE":
        text = _assemble_response(messages)
    elif stage == "JUDGE":
        text = json.dumps({"status": "pass", "notes": []})
    else:
        raise AssertionError(f"unexpected stage in this scenario: {stage}")
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _mock_mail_transport(captured: list[dict]) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"id": f"mock-{len(captured)}"})

    return httpx.MockTransport(_handler)


def _setup_project_with_all_and_managers_sources(database_url: str) -> tuple[UUID, UUID, UUID]:
    """manager・member が両方 notify_on_progress、all と managers_only のソースを持つ。"""

    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        member_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members "
            "(project_id, user_id, email, role, notify_on_progress) VALUES "
            "(%s, %s, 'manager@example.test', 'manager', true), "
            "(%s, %s, 'member@example.test', 'member', true)",
            (project_id, manager_id, project_id, member_id),
        )

        def _add_source(source_no: int, visibility: str, uploaded_by: UUID, text: str) -> None:
            source_id = conn.execute(
                "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
                "recorded_at, visibility, next_seq) VALUES (%s, %s, 'conversation', %s, now(), "
                "%s, 1) RETURNING id",
                (project_id, source_no, uploaded_by, visibility),
            ).fetchone()[0]
            version_id = conn.execute(
                "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
                "VALUES (%s, 1, 'h', %s) RETURNING id",
                (source_id, text),
            ).fetchone()[0]
            conn.execute(
                "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
                (version_id, source_id),
            )
            conn.execute(
                "INSERT INTO source_segments (project_id, source_version_id, label, seq, "
                "speaker, text, is_new, consumed) VALUES (%s, %s, %s, 1, 'user', %s, true, false)",
                (project_id, version_id, f"S{source_no}-1", text),
            )

        _add_source(1, "all", manager_id, _ALL_TEXT)
        _add_source(2, "managers_only", manager_id, _MANAGERS_SECRET_TEXT)
        conn.commit()
    return project_id, manager_id, member_id


def test_progress_mail_uses_all_summary_and_excludes_managers_only_text(
    migrated_database_url: str, worker_settings
) -> None:
    project_id, _manager_id, _member_id = _setup_project_with_all_and_managers_sources(
        migrated_database_url
    )
    with psycopg.connect(migrated_database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    captured: list[dict] = []
    transport = _mock_mail_transport(captured)

    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_fake_llm,
        mail_transport=transport,
    )

    assert outcome.outcome == "report_created"
    assert outcome.notify_allowed is True

    # 2人（manager・member）とも notify_on_progress=true なので、両方にメールが行く。
    assert len(captured) == 2
    for mail_payload in captured:
        assert "text" in mail_payload
        assert _SECRET_MARKER not in mail_payload["text"]
        assert "Xを採用しました" in mail_payload["text"]

    with psycopg.connect(migrated_database_url) as conn:
        log_rows = conn.execute(
            "SELECT kind, provider_message_id FROM notifications_log WHERE project_id = %s",
            (project_id,),
        ).fetchall()
        notif_rows = conn.execute(
            "SELECT kind FROM notifications WHERE project_id = %s", (project_id,)
        ).fetchall()
    assert len(log_rows) == 2
    assert all(kind == "report" and message_id is not None for kind, message_id in log_rows)
    assert len(notif_rows) == 2


def _make_agent_fake_llm_with_rogue_ask_member():
    """AGENT の呼び出しで、道具に無くても ask_member を呼ぼうとする偽の LLM を作る。

    防御の検査用（コードが道具を渡していなくても、実行を拒むかを確かめる）。
    """

    call_count = {"n": 0}

    def _fake(stage: str, messages, *, run, json_mode=False, tools=None):
        return _agent_fake_llm_with_rogue_ask_member(
            stage, messages, run=run, json_mode=json_mode, tools=tools, call_count=call_count
        )

    return _fake


def _agent_fake_llm_with_rogue_ask_member(
    stage: str, messages, *, run, json_mode=False, tools=None, call_count: dict
):
    if stage == "AGENT":
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LlmResult(
                text=None,
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "ask_member",
                            "arguments": json.dumps({"question": "この決定の理由を教えてください"}),
                        },
                    }
                ],
                input_tokens=5,
                output_tokens=5,
                model="fake",
                estimated=True,
            )
        return LlmResult(
            text=json.dumps({"resolved": False}),
            tool_calls=None,
            input_tokens=5,
            output_tokens=5,
            model="fake",
            estimated=True,
        )
    if stage == "EXTRACT":
        text = json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "役員報酬を引き上げる",
                        "reason": None,
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S2-1"],
                        "origin": "human_originated",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    elif stage == "SUPPORT":
        text = _support_response(messages)
    elif stage == "ASSEMBLE":
        text = _assemble_response(messages)
    elif stage == "JUDGE":
        text = json.dumps({"status": "pass", "notes": []})
    else:
        raise AssertionError(f"unexpected stage: {stage}")
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def test_managers_only_event_with_no_manager_uploader_never_asks_a_member(
    migrated_database_url: str, worker_settings
) -> None:
    """managers の組の根拠に manager がいなければ、質問は作られない。

    member が宛先になることもない【C-1】。
    """

    with psycopg.connect(migrated_database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        member_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) VALUES "
            "(%s, %s, 'manager@example.test', 'manager'), "
            "(%s, %s, 'member@example.test', 'member')",
            (project_id, manager_id, project_id, member_id),
        )
        # managers_only のソースの登録者は member（manager ではない）。
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'managers_only', 1) RETURNING id",
            (project_id, member_id),
        ).fetchone()[0]
        version_id = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', %s) RETURNING id",
            (source_id, _MANAGERS_SECRET_TEXT),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_id, source_id),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, "
            "speaker, text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, version_id, _MANAGERS_SECRET_TEXT),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    captured: list[dict] = []
    transport = _mock_mail_transport(captured)

    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_make_agent_fake_llm_with_rogue_ask_member(),
        mail_transport=transport,
    )

    assert outcome.outcome in ("report_created", "no_new_events")

    with psycopg.connect(migrated_database_url) as conn:
        questions = conn.execute(
            "SELECT asked_to FROM agent_questions WHERE project_id = %s", (project_id,)
        ).fetchall()
        question_notifications = conn.execute(
            "SELECT user_id FROM notifications WHERE project_id = %s AND kind = 'question'",
            (project_id,),
        ).fetchall()

    assert questions == []
    assert question_notifications == []
    # 質問が作られていないので、質問のメールも飛んでいない
    # （この組には all の出来事も無いため、進捗メールも飛ばない）。
    assert captured == []


def test_visibility_change_during_run_blocks_mail_and_in_app_notifications(
    migrated_database_url: str, worker_settings
) -> None:
    """実行中に可視性が変わると、レポートは保存されるがメール・通知は送られない【B-X2】。"""

    with psycopg.connect(migrated_database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role, notify_on_progress) "
            "VALUES (%s, %s, 'm@example.test', 'manager', true)",
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
            "VALUES (%s, 1, 'h', %s) RETURNING id",
            (source_id, _ALL_TEXT),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_id, source_id),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, "
            "speaker, text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, version_id, _ALL_TEXT),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    captured: list[dict] = []
    transport = _mock_mail_transport(captured)

    bumped = {"done": False}

    def _fake_llm_bumping(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        if stage == "EXTRACT" and not bumped["done"]:
            bumped["done"] = True
            with psycopg.connect(migrated_database_url) as bump_conn:
                bump_conn.execute(
                    "UPDATE projects SET visibility_epoch = visibility_epoch + 1 WHERE id = %s",
                    (project_id,),
                )
                bump_conn.commit()
        return _fake_llm(stage, messages, run=run, json_mode=json_mode, tools=tools)

    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_fake_llm_bumping,
        mail_transport=transport,
    )

    assert outcome.outcome == "report_created"
    assert outcome.notify_allowed is False
    assert captured == []

    with psycopg.connect(migrated_database_url) as conn:
        report_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
        log_count = conn.execute(
            "SELECT COUNT(*) FROM notifications_log WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
        notif_count = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
        needs_rebuild = conn.execute(
            "SELECT needs_rebuild FROM projects WHERE id = %s", (project_id,)
        ).fetchone()[0]
    assert report_count >= 1
    assert log_count == 0
    assert notif_count == 0
    assert needs_rebuild is True
