"""AD-11：質問の上限（1人あたり日本時間の1日に最大3問、全プロジェクト合計）。

同じ人に同じ日に4問目が作られると ``deferred`` になり、メールもアプリ内通知も
送られないこと。翌日の定期実行の最初（``send_deferred_questions``）で ``open``
になって送られること。
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import psycopg

from ai_hackathon_team_a import agent


def _mock_mail_transport(captured: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"id": f"mock-{len(captured)}"})

    return httpx.MockTransport(handler)


def _setup_project_with_visible_event(database_url: str) -> tuple[UUID, UUID, UUID]:
    """project・recipient(member)・「all」で見える出来事を1件用意する。"""

    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        recipient_id = uuid4()
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) "
            "VALUES (%s, %s, 'r@example.test', 'member')",
            (project_id, recipient_id),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'done') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 2) RETURNING id",
            (project_id, recipient_id),
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
            "true, true)",
            (project_id, version_id),
        )
        event_id = conn.execute(
            "INSERT INTO events (project_id, run_id, event_no, kind, summary, occurred_at, "
            "segment_ids, visibility_at_creation, partition) VALUES "
            "(%s, %s, 1, 'decision', 'Xを採用', now(), '{S1-1}', 'all', 'all') RETURNING id",
            (project_id, run_id),
        ).fetchone()[0]
        conn.commit()
    return project_id, recipient_id, event_id


def _make_candidate(event_id: UUID) -> agent.AgentCandidate:
    return agent.AgentCandidate(
        event_id=event_id,
        event_no=1,
        kind="decision",
        summary="Xを採用",
        reason=None,
        occurred_at=datetime.now(UTC),
        segment_ids=["S1-1"],
        partition="all",
    )


def test_fourth_question_of_the_day_is_deferred_and_not_sent(
    migrated_database_url: str, worker_settings
) -> None:
    project_id, recipient_id, event_id = _setup_project_with_visible_event(migrated_database_url)
    recipient = agent._Recipient(user_id=recipient_id, email="r@example.test")
    candidate = _make_candidate(event_id)
    captured: list[dict] = []

    with psycopg.connect(migrated_database_url) as conn:
        run_id = conn.execute(
            "SELECT id FROM runs WHERE project_id = %s LIMIT 1", (project_id,)
        ).fetchone()[0]

        # 今日すでに3問、この人に届いている(open)ことにしておく。
        for i in range(3):
            conn.execute(
                "INSERT INTO agent_questions "
                "(project_id, run_id, asked_to, question, related_event_id, partition, status) "
                "VALUES (%s, %s, %s, %s, %s, 'all', 'open')",
                (project_id, run_id, recipient_id, f"既存の質問{i}", event_id),
            )
        conn.commit()

        # 4問目：ask_member を直接呼ぶ(『役目が呼ぶ』private 関数、白箱テスト)。
        sent = agent._tool_ask_member(
            conn,
            project_id=project_id,
            run_id=run_id,
            candidate=candidate,
            recipient=recipient,
            question="4問目です",
            start_epoch=0,
            worker_settings=worker_settings,
            mail_transport=_mock_mail_transport(captured),
        )
        conn.commit()

        fourth = conn.execute(
            "SELECT status FROM agent_questions WHERE project_id = %s AND question = '4問目です'",
            (project_id,),
        ).fetchone()
        notif = conn.execute(
            "SELECT 1 FROM notifications WHERE project_id = %s AND user_id = %s "
            "AND kind = 'question' AND title LIKE %s",
            (project_id, recipient_id, "%確認したいこと%"),
        ).fetchall()

    assert sent is False
    assert fourth == ("deferred",)
    # 3問目までの通知(0件、この関数ではまだ送っていない)に加えて増えていない。
    assert notif == []
    assert captured == []  # メールも送られていない


def test_deferred_question_is_sent_by_next_days_cron_start(
    migrated_database_url: str, worker_settings
) -> None:
    project_id, recipient_id, event_id = _setup_project_with_visible_event(migrated_database_url)
    captured: list[dict] = []

    yesterday = datetime.now(UTC) - timedelta(days=1)
    today = datetime.now(UTC)

    with psycopg.connect(migrated_database_url) as conn:
        run_id = conn.execute(
            "SELECT id FROM runs WHERE project_id = %s LIMIT 1", (project_id,)
        ).fetchone()[0]
        question_id = conn.execute(
            "INSERT INTO agent_questions "
            "(project_id, run_id, asked_to, question, related_event_id, partition, status, "
            "created_at) VALUES (%s, %s, %s, 'なぜXを採用したのですか', %s, 'all', "
            "'deferred', %s) RETURNING id",
            (project_id, run_id, recipient_id, event_id, yesterday),
        ).fetchone()[0]
        conn.commit()

        sent_count = agent.send_deferred_questions(
            conn,
            worker_settings=worker_settings,
            mail_transport=_mock_mail_transport(captured),
            now=today,
        )
        conn.commit()

        status_after = conn.execute(
            "SELECT status FROM agent_questions WHERE id = %s", (question_id,)
        ).fetchone()
        notif = conn.execute(
            "SELECT 1 FROM notifications WHERE project_id = %s AND user_id = %s "
            "AND kind = 'question' AND question_id = %s",
            (project_id, recipient_id, question_id),
        ).fetchone()

    assert sent_count == 1
    assert status_after == ("open",)
    assert notif is not None
    assert len(captured) == 1
    assert "なぜXを採用したのですか" in captured[0]["text"]
