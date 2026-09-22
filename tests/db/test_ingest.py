"""ingest.py の DB テスト（設計書 §4、§3.1 の番号の振り方）。"""

from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from ai_hackathon_team_a import ingest
from ai_hackathon_team_a.worker_settings import WorkerSettings


def _insert_project(conn: psycopg.Connection) -> UUID:
    row = conn.execute(
        "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
    ).fetchone()
    return row[0]


@pytest.fixture
def settings(migrated_database_url: str) -> WorkerSettings:
    return WorkerSettings(
        _env_file=None,
        database_url=migrated_database_url,
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
        supabase_jwt_alg="ES256",
        resend_api_key="resend-key",
        mail_from="noreply@example.test",
        app_base_url="https://app.example.test",
        worker_shared_secret="w" * 32,
        cron_secret="c" * 32,
        daily_cost_limit_usd=5.0,
        system_alert_email="alert@example.test",
        app_env="test",
    )  # type: ignore[arg-type]


def _upload_ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json={}))


def test_ingest_conversation_creates_new_source_with_speakers(migrated_database_url: str) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        user_id = uuid4()

        result = ingest.ingest_conversation(
            conn,
            project_id=pid,
            uploaded_by=user_id,
            text="You: 予算はいくらですか\nChatGPT: 5万円です",
            recorded_at=None,
            visibility="all",
        )
        conn.commit()

        assert result.version_no == 1
        assert result.label_prefix == f"S{result.source_no}"

        rows = conn.execute(
            "SELECT label, seq, speaker FROM source_segments WHERE project_id = %s ORDER BY seq",
            (pid,),
        ).fetchall()
        assert [r[2] for r in rows] == ["user", "ai"]
        assert rows[0][0] == f"S{result.source_no}-1"
        assert rows[1][0] == f"S{result.source_no}-2"


def test_ingest_file_reupload_continues_seq_and_marks_consumed(
    migrated_database_url: str, settings: WorkerSettings
) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        user_id = uuid4()
        transport = _upload_ok_transport()

        first = ingest.ingest_file(
            conn,
            project_id=pid,
            uploaded_by=user_id,
            filename="notes.txt",
            data=b"paragraph one\n\nparagraph two",
            recorded_at=None,
            visibility="all",
            settings=settings,
            storage_transport=transport,
        )
        conn.commit()
        assert first.version_no == 1
        assert first.new_segment_count == 2

        second = ingest.ingest_file(
            conn,
            project_id=pid,
            uploaded_by=user_id,
            filename="notes.txt",
            data=b"paragraph one\n\nparagraph three",
            recorded_at=None,
            visibility="managers_only",  # 再アップロードでは無視される【C-3】
            settings=settings,
            storage_transport=transport,
        )
        conn.commit()

        assert second.source_id == first.source_id
        assert second.source_no == first.source_no
        assert second.version_no == 2

        rows = conn.execute(
            "SELECT label, seq, text, is_new, consumed FROM source_segments "
            "WHERE project_id = %s ORDER BY seq",
            (pid,),
        ).fetchall()
        # v1: S{n}-1, S{n}-2 のまま残る（追記専用、番号は変わらない）。
        assert [r[1] for r in rows] == [1, 2, 3, 4]
        assert rows[2][2] == "paragraph one" and rows[2][3] is False and rows[2][4] is True
        assert rows[3][2] == "paragraph three" and rows[3][3] is True and rows[3][4] is False

        # 可視性は再アップロードの入力を無視して変わらない【C-3】。
        visibility = conn.execute(
            "SELECT visibility FROM project_sources WHERE id = %s", (first.source_id,)
        ).fetchone()[0]
        assert visibility == "all"


def test_ingest_file_reupload_by_different_user_creates_separate_source(
    migrated_database_url: str, settings: WorkerSettings
) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        user_a, user_b = uuid4(), uuid4()
        transport = _upload_ok_transport()

        result_a = ingest.ingest_file(
            conn,
            project_id=pid,
            uploaded_by=user_a,
            filename="report.txt",
            data=b"from a",
            recorded_at=None,
            visibility="all",
            settings=settings,
            storage_transport=transport,
        )
        conn.commit()

        result_b = ingest.ingest_file(
            conn,
            project_id=pid,
            uploaded_by=user_b,
            filename="report.txt",
            data=b"from b",
            recorded_at=None,
            visibility="managers_only",
            settings=settings,
            storage_transport=transport,
        )
        conn.commit()

        assert result_a.source_id != result_b.source_id
        assert result_a.source_no != result_b.source_no
        assert result_b.version_no == 1  # b にとっては新規ソース（既存の版を上書きしない）


def test_ingest_file_unreadable_is_registered_without_segments(
    migrated_database_url: str, settings: WorkerSettings
) -> None:
    """抽出に失敗したファイルはエラーにせず、ファイル名だけを記録する（区切りは作らない）。"""

    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        result = ingest.ingest_file(
            conn,
            project_id=pid,
            uploaded_by=uuid4(),
            filename="broken.docx",
            data=b"not a zip file",
            recorded_at=None,
            visibility="all",
            settings=settings,
            storage_transport=_upload_ok_transport(),
        )
        conn.commit()

        assert result.extraction_failed is True
        assert result.segment_count == 0
        row = conn.execute(
            "SELECT filename FROM project_sources WHERE id = %s", (result.source_id,)
        ).fetchone()
        assert row == ("broken.docx",)
        count = conn.execute(
            "SELECT COUNT(*) FROM source_segments WHERE project_id = %s", (pid,)
        ).fetchone()[0]
        assert count == 0


def test_ingest_file_caps_segments_at_limit(
    migrated_database_url: str, settings: WorkerSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """区切りが上限を超えたら先頭から打ち切り、truncated を立てる。"""

    monkeypatch.setattr(ingest, "MAX_SEGMENTS_PER_FILE", 3)
    with psycopg.connect(migrated_database_url) as conn:
        pid = _insert_project(conn)
        result = ingest.ingest_file(
            conn,
            project_id=pid,
            uploaded_by=uuid4(),
            filename="many.txt",
            data="\n\n".join(f"段落{n}" for n in range(1, 6)).encode(),
            recorded_at=None,
            visibility="all",
            settings=settings,
            storage_transport=_upload_ok_transport(),
        )
        conn.commit()

        assert result.truncated is True
        texts = [
            r[0]
            for r in conn.execute(
                "SELECT text FROM source_segments WHERE project_id = %s ORDER BY seq", (pid,)
            ).fetchall()
        ]
        assert texts == ["段落1", "段落2", "段落3"]
