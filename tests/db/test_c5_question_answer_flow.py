"""C5相当：理由の無い決定 → 質問（アプリ内＋メール）→ 回答 → 次の実行で理由が

根拠つきで載る（設計書 §5.3 (7)(12)、§5.4、AD-1 U5）。
"""

import json
import re
from uuid import UUID

import httpx
import psycopg
import pytest

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.api.notifications import AnswerQuestionRequest, answer_question
from ai_hackathon_team_a.auth import AuthenticatedUser
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

_ORIGINAL_TEXT = "Xを採用することにした。"
_ANSWER_TEXT = "予算内に収まるのでXを採用することにしました。"


def _messages_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def _support_response(messages: list[dict]) -> str:
    count = _messages_text(messages).count("## 出来事")
    return json.dumps({"results": ["supported" for _ in range(max(count, 1))]})


def _assemble_response() -> str:
    return json.dumps(
        {
            "sections": {
                "purpose": [{"text": "目的はG", "event_nos": [1]}],
                "current_state": [{"text": "Xを採用した", "event_nos": [1]}],
                "direction": [],
            },
            "summary_for_mail": "要約",
        }
    )


def _make_run1_llm(call_count: dict):
    def _fake(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        if stage == "EXTRACT":
            text = json.dumps(
                {
                    "events": [
                        {
                            "kind": "decision",
                            "summary": "Xを採用",
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
        elif stage == "SUPPORT":
            text = _support_response(messages)
        elif stage == "ASSEMBLE":
            text = _assemble_response()
        elif stage == "JUDGE":
            text = json.dumps({"status": "pass", "notes": []})
        elif stage == "AGENT":
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
                                "arguments": json.dumps({"question": "なぜXを採用したのですか？"}),
                            },
                        }
                    ],
                    input_tokens=5,
                    output_tokens=5,
                    model="fake",
                    estimated=True,
                )
            text = json.dumps({"resolved": False})
        else:
            raise AssertionError(f"unexpected stage: {stage}")
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=10,
            output_tokens=10,
            model="fake",
            estimated=True,
        )

    return _fake


def _run2_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    if stage == "EXTRACT":
        text = _messages_text(messages)
        match = re.search(r"\[(\d+)\] decision", text)
        assert match is not None, "元の決定（理由なし）が今も有効な出来事として見えているはず"
        original_no = int(match.group(1))
        labels = re.findall(r'label="([^"]+)"', text)
        assert labels, "回答の区切りが渡されているはず"
        answer_label = labels[0]
        payload = json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "Xを採用（理由判明）",
                        "reason": "予算内に収まるから",
                        "occurred_at": "2026-09-21T11:00:00+09:00",
                        "segment_ids": [answer_label],
                        "origin": "human_originated",
                        "supersedes_event_no": original_no,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    elif stage == "SUPPORT":
        payload = _support_response(messages)
    elif stage == "ASSEMBLE":
        payload = _assemble_response()
    elif stage == "JUDGE":
        payload = json.dumps({"status": "pass", "notes": []})
    else:
        raise AssertionError(f"unexpected stage: {stage}")
    return LlmResult(
        text=payload,
        tool_calls=None,
        input_tokens=10,
        output_tokens=10,
        model="fake",
        estimated=True,
    )


def _mock_mail_transport(captured: list[dict]) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"id": f"mock-{len(captured)}"})

    return httpx.MockTransport(_handler)


def _setup_project(database_url: str) -> tuple[UUID, UUID]:
    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        uploader_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) "
            "VALUES (%s, %s, 'uploader@example.test', 'member')",
            (project_id, uploader_id),
        )
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, uploader_id),
        ).fetchone()[0]
        version_id = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', %s) RETURNING id",
            (source_id, _ORIGINAL_TEXT),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_id, source_id),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, "
            "speaker, text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, version_id, _ORIGINAL_TEXT),
        )
        # 手で source_no=1 を使ったので、ingest.ingest_answer が払い出す次の番号と
        # 衝突しないよう、projects.next_source_no を進めておく。
        conn.execute("UPDATE projects SET next_source_no = 2 WHERE id = %s", (project_id,))
        conn.commit()
    return project_id, uploader_id


def test_question_created_then_answer_supersedes_with_reason(
    migrated_database_url: str, worker_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, uploader_id = _setup_project(migrated_database_url)
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
    call_count = {"n": 0}

    outcome1 = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_make_run1_llm(call_count),
        mail_transport=transport,
    )
    assert outcome1.outcome in ("report_created", "no_new_events")

    with psycopg.connect(migrated_database_url) as conn:
        question_row = conn.execute(
            "SELECT id, asked_to, status FROM agent_questions WHERE project_id = %s", (project_id,)
        ).fetchone()
        notif_row = conn.execute(
            "SELECT user_id, kind FROM notifications WHERE project_id = %s AND kind = 'question'",
            (project_id,),
        ).fetchone()

    assert question_row is not None
    question_id, asked_to, status = question_row
    assert asked_to == uploader_id
    assert status == "open"
    assert notif_row is not None
    assert notif_row[0] == uploader_id
    # 質問のメールが飛んでいる（アプリ内通知＋メール。進捗メールも同時に飛ぶ）。
    question_mails = [m for m in captured if "questions" in m["text"]]
    assert len(question_mails) == 1

    # --- 回答（W23 相当）。AD-1 U5：回答はアプリ内の回答欄のみ。 ---
    # answer_question は内部で本物の run.execute_run（本物の call_llm）を直接呼ぶため、
    # 本物の OrcaRouter・Resend に一切触れないよう、run.execute_run をこのテストの間だけ
    # 「偽の LLM を使う」ラッパーに差し替える（実装（api/notifications.py）は変えない）。
    real_execute_run = run_module.execute_run

    def _execute_run_with_fake_llm(run_id, *, worker_settings, pool, **_ignored):
        return real_execute_run(
            run_id,
            worker_settings=worker_settings,
            pool=pool,
            llm_call=_run2_llm,
            mail_transport=transport,
        )

    monkeypatch.setattr(run_module, "execute_run", _execute_run_with_fake_llm)

    with psycopg.connect(migrated_database_url) as conn:
        result = answer_question(
            question_id,
            AnswerQuestionRequest(text=_ANSWER_TEXT),
            user=AuthenticatedUser(user_id=uploader_id),
            conn=conn,
            settings=worker_settings,
        )
        assert result["source_id"] is not None

    with psycopg.connect(migrated_database_url) as conn:
        status_after = conn.execute(
            "SELECT status, answer_source_id FROM agent_questions WHERE id = %s", (question_id,)
        ).fetchone()
        answer_source = conn.execute(
            "SELECT type, visibility, uploaded_by FROM project_sources WHERE id = %s",
            (status_after[1],),
        ).fetchone()

    assert status_after[0] == "answered"
    assert answer_source == ("answer", "all", uploader_id)

    with psycopg.connect(migrated_database_url) as conn:
        superseding = conn.execute(
            "SELECT reason, segment_ids FROM events "
            "WHERE project_id = %s AND supersedes_event_id IS NOT NULL",
            (project_id,),
        ).fetchone()
    assert superseding is not None
    reason, segment_ids = superseding
    assert reason == "予算内に収まるから"
    assert any(sid.startswith("S2-") for sid in segment_ids)  # 回答は新しいソース S2
