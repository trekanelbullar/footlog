"""LLM 呼び出しの唯一の入口（設計書 §6.1、不変条件 I6・I7）。

``OrcaClient`` を直接呼ぶコードは、ここと ``clients/`` 以外に書かない
（``tests/invariants/test_i7_single_entry.py`` が静的に確かめる）。呼び出しの前に
``daily_costs`` の行ロックで予算を予約し、上限を超えるなら呼ばずに
:class:`CostLimitExceeded` を送出する。呼び出しの後は成否にかかわらず予約を戻し、
実績を加算し、``model_calls_log`` に追記する。送る直前にメッセージ全体へ
``redact.redact`` をもう一度かける（I6 の二重化）。
"""

import time
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from ai_hackathon_team_a import pricing
from ai_hackathon_team_a.clients.orca import Completion, OrcaClient
from ai_hackathon_team_a.config import Settings, Stage, get_settings
from ai_hackathon_team_a.db import ConnectionPool, get_pool
from ai_hackathon_team_a.redact import redact
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

_JST = ZoneInfo("Asia/Tokyo")


class CostLimitExceeded(RuntimeError):
    """その日の DAILY_COST_LIMIT_USD を超えるため、呼び出しを行わなかったことを表す。"""


@dataclass(frozen=True)
class RunContext:
    """``model_calls_log`` に記録するための実行の文脈。"""

    project_id: UUID
    run_id: UUID


@dataclass(frozen=True)
class LlmResult:
    """``call_llm`` の戻り値。"""

    text: str
    tool_calls: list[dict[str, object]] | None
    input_tokens: int
    output_tokens: int
    model: str
    estimated: bool


class AlertFn(Protocol):
    def __call__(self, worker_settings: WorkerSettings, *, alert_date: date) -> None: ...


def send_cost_alert_email(worker_settings: WorkerSettings, *, alert_date: date) -> None:
    """コスト上限超過の通知を送る（差し替え可能。メール送信の実体は7段目で実装する）。"""


@lru_cache
def _cached_orca_client() -> OrcaClient:
    return OrcaClient(get_settings())


def _today_jst() -> date:
    return datetime.now(_JST).date()


def _redact_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """メッセージ全体にもう一度伏せ字をかける（I6 の二重化）。"""

    redacted: list[dict[str, object]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            clean_text, _ = redact(content)
            redacted.append({**message, "content": clean_text})
        else:
            redacted.append(dict(message))
    return redacted


def _message_chars(messages: list[dict[str, object]]) -> int:
    return sum(len(m.get("content", "")) for m in messages if isinstance(m.get("content"), str))


def _estimate_input_tokens_conservative(chars: int) -> int:
    """文字数からの保守的な（多めの）トークン数の概算。

    実際のトークナイザは概ね1トークンあたり2文字以上（日本語でも）になるため、
    1トークン=2文字という多めの比率で見積もり、最大コストを低く見誤らないようにする。
    """

    return max(1, -(-chars // 2))  # ceil(chars / 2)


def _cost_usd(*, input_tokens: int, output_max_tokens: int, price: pricing.ModelPrice) -> float:
    return (input_tokens / 1_000_000) * price.input_per_million_usd + (
        output_max_tokens / 1_000_000
    ) * price.output_per_million_usd


def _reserve_budget(
    pool: ConnectionPool,
    *,
    today: date,
    max_cost_usd: float,
    daily_limit_usd: float,
    alert_fn: AlertFn,
    worker_settings: WorkerSettings,
) -> None:
    """``daily_costs`` を行ロックし、収まれば予約する。超えるなら呼び出さない（I7、C-X1）。"""

    exceeded = False
    first_time_over_limit = False

    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO daily_costs (cost_date, reserved_usd, spent_usd) "
            "VALUES (%s, 0, 0) ON CONFLICT (cost_date) DO NOTHING",
            (today,),
        )
        row = conn.execute(
            "SELECT reserved_usd, spent_usd FROM daily_costs WHERE cost_date = %s FOR UPDATE",
            (today,),
        ).fetchone()
        reserved_usd, spent_usd = float(row[0]), float(row[1])

        if spent_usd + reserved_usd + max_cost_usd > daily_limit_usd:
            exceeded = True
            inserted = conn.execute(
                "INSERT INTO daily_cost_alerts (alert_date, notified) VALUES (%s, true) "
                "ON CONFLICT (alert_date) DO NOTHING RETURNING alert_date",
                (today,),
            ).fetchone()
            first_time_over_limit = inserted is not None
        else:
            conn.execute(
                "UPDATE daily_costs SET reserved_usd = reserved_usd + %s WHERE cost_date = %s",
                (max_cost_usd, today),
            )

    if exceeded:
        if first_time_over_limit:
            alert_fn(worker_settings, alert_date=today)
        raise CostLimitExceeded(
            f"daily cost limit ${daily_limit_usd:.4f} would be exceeded on {today.isoformat()}"
        )


def _settle_budget_and_log(
    pool: ConnectionPool,
    *,
    today: date,
    max_cost_usd: float,
    actual_cost_usd: float,
    run: RunContext,
    stage: Stage,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    ok: bool,
) -> None:
    """予約を戻して実績を加算し、``model_calls_log`` に追記する（本文は記録しない）。"""

    with pool.connection() as conn:
        conn.execute(
            "UPDATE daily_costs SET reserved_usd = reserved_usd - %s, spent_usd = spent_usd + %s "
            "WHERE cost_date = %s",
            (max_cost_usd, actual_cost_usd, today),
        )
        conn.execute(
            """
            INSERT INTO model_calls_log
                (project_id, run_id, stage, model, input_tokens, output_tokens,
                 estimated_cost_usd, latency_ms, ok)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run.project_id,
                run.run_id,
                stage,
                model,
                input_tokens,
                output_tokens,
                actual_cost_usd,
                latency_ms,
                ok,
            ),
        )


def call_llm(
    stage: Stage,
    messages: list[dict[str, object]],
    *,
    run: RunContext,
    json_mode: bool = False,
    tools: list[dict[str, object]] | None = None,
    orca_client: OrcaClient | None = None,
    settings: Settings | None = None,
    worker_settings: WorkerSettings | None = None,
    pool: ConnectionPool | None = None,
    alert_fn: AlertFn | None = None,
) -> LlmResult:
    """LLM 呼び出しの唯一の入口（設計書 §6.1）。

    ``orca_client``・``settings``・``worker_settings``・``pool``・``alert_fn`` は
    テストのための差し替え口で、省略時はそれぞれの既定（本物）を使う。
    """

    settings = settings or get_settings()
    worker_settings = worker_settings or get_worker_settings()
    pool = pool or get_pool(worker_settings)
    orca_client = orca_client or _cached_orca_client()
    alert_fn = alert_fn or send_cost_alert_email

    stage_config = settings.stage_config(stage)
    price = pricing.get_price(stage_config.model)

    redacted_messages = _redact_messages(messages)
    estimated_input_tokens = _estimate_input_tokens_conservative(_message_chars(redacted_messages))
    max_cost_usd = _cost_usd(
        input_tokens=estimated_input_tokens,
        output_max_tokens=stage_config.max_tokens,
        price=price,
    )

    today = _today_jst()
    _reserve_budget(
        pool,
        today=today,
        max_cost_usd=max_cost_usd,
        daily_limit_usd=worker_settings.daily_cost_limit_usd,
        alert_fn=alert_fn,
        worker_settings=worker_settings,
    )

    started = time.monotonic()
    ok = True
    completion: Completion | None = None
    try:
        completion = orca_client.complete(
            redacted_messages,
            model=stage_config.model,
            max_tokens=stage_config.max_tokens,
            reasoning=stage_config.reasoning,
            json_mode=json_mode,
            tools=tools,
        )
        return LlmResult(
            text=completion.text,
            tool_calls=completion.tool_calls,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            model=completion.model,
            estimated=completion.estimated,
        )
    except Exception:
        ok = False
        raise
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        if completion is not None:
            actual_cost_usd = _cost_usd(
                input_tokens=completion.input_tokens,
                output_max_tokens=completion.output_tokens,
                price=price,
            )
            input_tokens, output_tokens = completion.input_tokens, completion.output_tokens
        else:
            actual_cost_usd = max_cost_usd
            input_tokens, output_tokens = estimated_input_tokens, 0
        _settle_budget_and_log(
            pool,
            today=today,
            max_cost_usd=max_cost_usd,
            actual_cost_usd=actual_cost_usd,
            run=run,
            stage=stage,
            model=stage_config.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            ok=ok,
        )
