"""I7（動かしての検査）：上限超過時、DB 以外への接続が1回も起きない（設計書 §6.1、【C-X2】）。

``daily_costs`` を上限に達した状態にし、OS のソケット接続を差し替えて外への接続を
記録する。その状態で手動実行をひと通り動かし、DB（テスト用）以外への接続が
1回も起きないことを確かめる。
"""

import functools
import socket
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.config import Settings
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import CostLimitExceeded, call_llm

_JST = ZoneInfo("Asia/Tokyo")


class _NeverCalledOrcaClient:
    """呼ばれたら失敗させる偽のクライアント（コスト上限で呼ばれないことの証明）。"""

    def complete(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("OrcaClient.complete must not be called when over budget")


@pytest.fixture
def blocked_socket_connect(monkeypatch: pytest.MonkeyPatch):
    """``socket.socket.connect`` を差し替え、接続先の host:port を全部記録する。

    テスト用 Postgres への接続（``127.0.0.1``・``localhost``）だけは通す
    （そうしないとテスト自体が動かない）。それ以外への接続があれば記録に残る。
    """

    connections: list[tuple[str, int] | str] = []
    original_connect = socket.socket.connect

    def _fake_connect(self, address, *args, **kwargs):  # noqa: ANN001
        if isinstance(address, tuple) and len(address) >= 2:
            host = address[0]
            connections.append((host, address[1]))
            if host not in ("127.0.0.1", "localhost", "::1"):
                raise AssertionError(f"unexpected outbound connection to {address!r}")
        else:
            connections.append(str(address))
        return original_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _fake_connect)
    return connections


def test_manual_execution_makes_no_non_db_connections_when_over_cost_limit(
    migrated_database_url: str, worker_settings, blocked_socket_connect
) -> None:
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 0.01})
    pool = ConnectionPool(migrated_database_url)

    today = datetime.now(_JST).date()
    with psycopg.connect(migrated_database_url) as conn:
        conn.execute(
            "INSERT INTO daily_costs (cost_date, reserved_usd, spent_usd) VALUES (%s, 0, 999)",
            (today,),
        )
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        user_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) "
            "VALUES (%s, %s, 'm@example.test', 'manager')",
            (project_id, user_id),
        )
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, user_id),
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
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', 'X予算はResend', "
            "true, false)",
            (project_id, version_id),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    # 実際の .env・OrcaRouter には一切触れない、偽の設定・偽のクライアントだけを渡す
    # （call_llm の既定は本物の設定・クライアントを使うため、ここで固定する）。
    fake_settings = Settings(_env_file=None, api_key="test-key")
    fake_llm_call = functools.partial(
        call_llm,
        orca_client=_NeverCalledOrcaClient(),
        settings=fake_settings,
        worker_settings=worker_settings,
        pool=pool,
    )

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=fake_llm_call
    )

    assert outcome.outcome == "cost_limited"
    non_local = [c for c in blocked_socket_connect if isinstance(c, str)]
    assert non_local == []


def test_call_llm_raises_before_touching_network_when_over_limit(
    migrated_database_url: str, worker_settings, blocked_socket_connect
) -> None:
    from ai_hackathon_team_a.llm import RunContext

    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 0.0000001})
    pool = ConnectionPool(migrated_database_url)
    # 実際の .env・OrcaRouter には一切触れない、偽の設定・偽のクライアントだけを渡す。
    settings = Settings(_env_file=None, api_key="test-key")

    with psycopg.connect(migrated_database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'running') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    with pytest.raises(CostLimitExceeded):
        call_llm(
            "EXTRACT",
            [{"role": "user", "content": "hello"}],
            run=RunContext(project_id=project_id, run_id=run_id),
            json_mode=True,
            orca_client=_NeverCalledOrcaClient(),
            settings=settings,
            worker_settings=worker_settings,
            pool=pool,
        )

    non_local = [c for c in blocked_socket_connect if isinstance(c, str)]
    assert non_local == []
