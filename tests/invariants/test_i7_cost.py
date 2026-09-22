"""I7：コスト上限を超えたら LLM を呼ばない（設計書 §6.1、【C-X1】）。"""

import threading
import time

import psycopg
import pytest

from ai_hackathon_team_a.clients.orca import Completion
from ai_hackathon_team_a.config import Settings
from ai_hackathon_team_a.llm import CostLimitExceeded, RunContext, call_llm


def _insert_run(database_url: str) -> RunContext:
    """``model_calls_log`` の外部キーを満たすため、実在する project/run を作る。"""

    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'running') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    return RunContext(project_id=project_id, run_id=run_id)


class _FakeOrcaClient:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def complete(
        self, messages, *, model, max_tokens, reasoning="off", json_mode=False, tools=None
    ):
        with self._lock:
            self.calls += 1
        # 予約 → 実呼び出し → 精算の間を広げ、2スレッドの予約チェックが確実に
        # 重なるようにする（実際のコストは予約した最大コストよりずっと小さいため、
        # 素早く精算されると2回目の予約が「空いた」と誤判定してしまう）。
        time.sleep(0.2)
        return Completion(
            text="{}", tool_calls=None, input_tokens=10, output_tokens=10, model=model
        )


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, api_key="test-key", model="qwen/qwen3.7-flash")


def test_no_calls_when_already_over_limit(
    migrated_database_url: str, worker_settings, settings: Settings
) -> None:
    from ai_hackathon_team_a.db import ConnectionPool

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 0.000001})
    orca_client = _FakeOrcaClient()
    run = _insert_run(migrated_database_url)

    with pytest.raises(CostLimitExceeded):
        call_llm(
            "EXTRACT",
            [{"role": "user", "content": "hello"}],
            run=run,
            json_mode=True,
            orca_client=orca_client,
            settings=settings,
            worker_settings=worker_settings,
            pool=pool,
            # 上限超過アラートの送信（本物の Resend への接続）はここでは確かめない。
            alert_fn=lambda *_a, **_k: None,
        )

    assert orca_client.calls == 0


def test_only_one_of_two_concurrent_calls_goes_through_when_budget_has_room_for_one(
    migrated_database_url: str, worker_settings, settings: Settings
) -> None:
    from ai_hackathon_team_a.db import ConnectionPool

    pool = ConnectionPool(migrated_database_url)
    # 1回分だけ通る上限にする（1回分の最大コストのすぐ上）。call_llm 自身が使うのと
    # 同じ見積もり関数（文字数からの保守的な概算）で、正確に1回分ぶんだけ計算する。
    from ai_hackathon_team_a import pricing
    from ai_hackathon_team_a.llm import _cost_usd, _estimate_input_tokens_conservative

    message_content = "x" * 50
    stage_config = settings.stage_config("EXTRACT")
    price = pricing.get_price(stage_config.model)
    estimated_input_tokens = _estimate_input_tokens_conservative(len(message_content))
    one_call_cost = _cost_usd(
        input_tokens=estimated_input_tokens,
        output_max_tokens=stage_config.max_tokens,
        price=price,
    )
    worker_settings = worker_settings.model_copy(
        update={"daily_cost_limit_usd": float(one_call_cost) * 1.5}
    )
    orca_client = _FakeOrcaClient()
    run = _insert_run(migrated_database_url)

    barrier = threading.Barrier(2)
    results: list[str] = []

    def _worker() -> None:
        barrier.wait()
        try:
            call_llm(
                "EXTRACT",
                [{"role": "user", "content": message_content}],
                run=run,
                json_mode=True,
                orca_client=orca_client,
                settings=settings,
                worker_settings=worker_settings,
                pool=pool,
                alert_fn=lambda *_a, **_k: None,
            )
            results.append("ok")
        except CostLimitExceeded:
            results.append("exceeded")

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert orca_client.calls <= 1
    assert results.count("ok") <= 1
    assert results.count("exceeded") >= 1


def test_reserved_and_spent_are_settled_after_a_successful_call(
    migrated_database_url: str, worker_settings, settings: Settings
) -> None:
    from ai_hackathon_team_a.db import ConnectionPool

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 5.0})
    orca_client = _FakeOrcaClient()
    run = _insert_run(migrated_database_url)

    call_llm(
        "EXTRACT",
        [{"role": "user", "content": "hello"}],
        run=run,
        json_mode=True,
        orca_client=orca_client,
        settings=settings,
        worker_settings=worker_settings,
        pool=pool,
    )

    with psycopg.connect(migrated_database_url) as conn:
        row = conn.execute("SELECT reserved_usd, spent_usd FROM daily_costs").fetchone()
        log_row = conn.execute(
            "SELECT stage, model, input_tokens, output_tokens, ok FROM model_calls_log"
        ).fetchone()

    assert float(row[0]) == 0.0  # 予約は使い切って戻っている
    assert float(row[1]) > 0.0
    assert log_row == ("EXTRACT", "qwen/qwen3.7-flash", 10, 10, True)
