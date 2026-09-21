"""W20〜W23 の権限テスト（設計書 §2.5・§5.4、【C-1】【C-2】）。"""

from collections.abc import Callable
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.llm import LlmResult

AuthHeaders = Callable[..., dict[str, str]]


def _insert_project(conn: psycopg.Connection) -> UUID:
    return conn.execute(
        "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
    ).fetchone()[0]


def _insert_member(conn: psycopg.Connection, *, project_id: UUID, user_id: UUID, role: str) -> None:
    conn.execute(
        "INSERT INTO project_members (project_id, user_id, email, role) VALUES (%s, %s, %s, %s)",
        (project_id, user_id, f"{user_id}@example.test", role),
    )


def _insert_notification(
    conn: psycopg.Connection, *, project_id: UUID, user_id: UUID, kind: str = "report"
) -> UUID:
    return conn.execute(
        "INSERT INTO notifications (user_id, project_id, kind, title) "
        "VALUES (%s, %s, %s, 'タイトル') RETURNING id",
        (user_id, project_id, kind),
    ).fetchone()[0]


def _insert_event(
    conn: psycopg.Connection, *, project_id: UUID, run_id: UUID, partition: str
) -> UUID:
    return conn.execute(
        "INSERT INTO events (project_id, run_id, event_no, kind, summary, occurred_at, "
        "segment_ids, visibility_at_creation, partition) "
        "VALUES (%s, %s, 1, 'decision', 'ある決定', now(), '{}', %s, %s) RETURNING id",
        (project_id, run_id, "all" if partition == "all" else "managers_only", partition),
    ).fetchone()[0]


def _insert_run(conn: psycopg.Connection, *, project_id: UUID) -> UUID:
    return conn.execute(
        "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'done') RETURNING id",
        (project_id,),
    ).fetchone()[0]


def _insert_question(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    run_id: UUID,
    asked_to: UUID,
    related_event_id: UUID,
    partition: str = "all",
    status: str = "open",
) -> UUID:
    return conn.execute(
        "INSERT INTO agent_questions "
        "(project_id, run_id, asked_to, question, related_event_id, partition, status) "
        "VALUES (%s, %s, %s, '質問文', %s, %s, %s) RETURNING id",
        (project_id, run_id, asked_to, related_event_id, partition, status),
    ).fetchone()[0]


# ---- W20 ------------------------------------------------------------------


def test_w20_lists_only_own_notifications(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_a, user_b = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_notification(conn, project_id=pid, user_id=user_a)
        _insert_notification(conn, project_id=pid, user_id=user_b)
        conn.commit()

    response = api_client.get("/internal/me/notifications", headers=auth_headers(user_a))
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1


# ---- W21 ------------------------------------------------------------------


def test_w21_marks_own_notification_read_and_404s_for_others(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_a, user_b = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        nid = _insert_notification(conn, project_id=pid, user_id=user_a)
        conn.commit()

    forbidden = api_client.post(
        f"/internal/me/notifications/{nid}/read", headers=auth_headers(user_b)
    )
    assert forbidden.status_code == 404

    ok = api_client.post(f"/internal/me/notifications/{nid}/read", headers=auth_headers(user_a))
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}

    missing = api_client.post(
        f"/internal/me/notifications/{uuid4()}/read", headers=auth_headers(user_a)
    )
    assert missing.status_code == 404


# ---- W22 ------------------------------------------------------------------


def test_w22_returns_question_only_to_asked_to(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_a, user_b = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_a, role="manager")
        run_id = _insert_run(conn, project_id=pid)
        event_id = _insert_event(conn, project_id=pid, run_id=run_id, partition="all")
        qid = _insert_question(
            conn, project_id=pid, run_id=run_id, asked_to=user_a, related_event_id=event_id
        )
        conn.commit()

    forbidden = api_client.get(f"/internal/questions/{qid}", headers=auth_headers(user_b))
    assert forbidden.status_code == 404

    ok = api_client.get(f"/internal/questions/{qid}", headers=auth_headers(user_a))
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "open"
    assert body["question"] == "質問文"


def test_w22_expires_managers_question_when_asked_to_is_no_longer_manager(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    """【C-1】：表示する時点で実効の可視性・立場を再確認し、見えなければ expired にする。"""

    manager_id = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=manager_id, role="manager")
        run_id = _insert_run(conn, project_id=pid)
        event_id = _insert_event(conn, project_id=pid, run_id=run_id, partition="managers")
        qid = _insert_question(
            conn,
            project_id=pid,
            run_id=run_id,
            asked_to=manager_id,
            related_event_id=event_id,
            partition="managers",
        )
        conn.commit()

    # manager から降格される（もう1人 manager を足してから降格：last_manager を避ける）。
    with psycopg.connect(migrated_database_url) as conn:
        other_manager = uuid4()
        _insert_member(conn, project_id=pid, user_id=other_manager, role="manager")
        conn.execute(
            "UPDATE project_members SET role = 'member' WHERE project_id = %s AND user_id = %s",
            (pid, manager_id),
        )
        conn.commit()

    response = api_client.get(f"/internal/questions/{qid}", headers=auth_headers(manager_id))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "expired"
    assert body["question"] == ""

    with psycopg.connect(migrated_database_url) as conn:
        status = conn.execute(
            "SELECT status FROM agent_questions WHERE id = %s", (qid,)
        ).fetchone()[0]
    assert status == "expired"


# ---- W23 ------------------------------------------------------------------


def test_w23_rejects_other_users_and_non_open_questions(
    api_client: TestClient, migrated_database_url: str, auth_headers: AuthHeaders
) -> None:
    user_a, user_b = uuid4(), uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_a, role="manager")
        run_id = _insert_run(conn, project_id=pid)
        event_id = _insert_event(conn, project_id=pid, run_id=run_id, partition="all")
        qid_open = _insert_question(
            conn, project_id=pid, run_id=run_id, asked_to=user_a, related_event_id=event_id
        )
        qid_answered = _insert_question(
            conn,
            project_id=pid,
            run_id=run_id,
            asked_to=user_a,
            related_event_id=event_id,
            status="answered",
        )
        conn.commit()

    forbidden = api_client.post(
        f"/internal/questions/{qid_open}/answer",
        json={"text": "回答"},
        headers=auth_headers(user_b),
    )
    assert forbidden.status_code == 404

    already_answered = api_client.post(
        f"/internal/questions/{qid_answered}/answer",
        json={"text": "回答"},
        headers=auth_headers(user_a),
    )
    assert already_answered.status_code == 409


def test_w23_success_ingests_answer_and_cannot_be_answered_twice(
    api_client: TestClient,
    migrated_database_url: str,
    auth_headers: AuthHeaders,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_a = uuid4()
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        _insert_member(conn, project_id=pid, user_id=user_a, role="manager")
        run_id = _insert_run(conn, project_id=pid)
        event_id = _insert_event(conn, project_id=pid, run_id=run_id, partition="all")
        qid = _insert_question(
            conn, project_id=pid, run_id=run_id, asked_to=user_a, related_event_id=event_id
        )
        conn.commit()

    # 本物の call_llm（本物の OrcaRouter・Resend）には一切触れないよう、回答が
    # トリガーする実行を、偽の LLM を使うものに差し替える。
    real_execute_run = run_module.execute_run

    def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        import json as _json

        text = {
            "EXTRACT": _json.dumps({"events": [], "suspected_injection_segment_ids": []}),
        }.get(stage, "{}")
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=1,
            output_tokens=1,
            model="fake",
            estimated=True,
        )

    def _execute_run_with_fake_llm(run_id, *, worker_settings, pool, **_ignored):
        return real_execute_run(
            run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
        )

    monkeypatch.setattr(run_module, "execute_run", _execute_run_with_fake_llm)

    response = api_client.post(
        f"/internal/questions/{qid}/answer",
        json={"text": "回答本文です"},
        headers=auth_headers(user_a),
    )
    assert response.status_code == 200
    assert "source_id" in response.json()

    with psycopg.connect(migrated_database_url) as conn:
        status = conn.execute(
            "SELECT status FROM agent_questions WHERE id = %s", (qid,)
        ).fetchone()[0]
    assert status == "answered"

    again = api_client.post(
        f"/internal/questions/{qid}/answer",
        json={"text": "もう一度"},
        headers=auth_headers(user_a),
    )
    assert again.status_code == 409
