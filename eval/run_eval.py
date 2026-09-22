"""LLM モデル比較の評価ランナー（eval 専用。本番コードは読むだけで変更しない）。

本番の純粋関数（``extract_events``・``check_support``・``assemble_report``・
``judge_report``・``assemble_and_judge``）に、``llm`` 引数だけ評価用のクライアントを
差し替えて渡す。プロンプトも本番の ``prompts/*.txt`` をそのまま使う。

- 採点はすべてこのファイルのコード（決定的）で行い、LLM は使わない。
- DB（Supabase）には一切つながない。費用は ``eval/results/calls.jsonl`` に積算し、
  プロセスをまたいで上限（既定 5 USD）を守る。
- 接続先・鍵は ``ai_hackathon_team_a.config.get_settings()`` がプログラム内で読む。

使い方（リポジトリ直下で）::

    uv run python eval/run_eval.py smoke
    uv run python eval/run_eval.py extract              # 段階1
    uv run python eval/run_eval.py extract --reasoning low --models a,b
    uv run python eval/run_eval.py stage2               # 段階2（最安3モデル）
    uv run python eval/run_eval.py configs --good X --cheap Y   # 段階3（A/B/C）
    uv run python eval/run_eval.py report

同じ単位（段階・モデル・ケース・繰り返し）は ``runs.jsonl`` にあれば飛ばすので、
途中で止まっても再実行で続きから流せる。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from openai import APIStatusError, APITimeoutError, OpenAI, OpenAIError

from ai_hackathon_team_a.config import get_settings
from ai_hackathon_team_a.pipeline.assemble import assemble_report
from ai_hackathon_team_a.pipeline.checks import apply_mechanical_checks
from ai_hackathon_team_a.pipeline.contracts import (
    DIFF_HEADINGS,
    AssembleInput,
    AssembleOutput,
    EventSummary,
    ExtractedEvent,
    ExtractInput,
    SegmentIn,
    Sentence,
    SupportInput,
    SupportItem,
)
from ai_hackathon_team_a.pipeline.extract import extract_events
from ai_hackathon_team_a.pipeline.judge import assemble_and_judge, judge_report
from ai_hackathon_team_a.pipeline.support import check_support

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
CONFIG_PATH = EVAL_DIR / "eval-config.json"
MODELS_MD = RESULTS_DIR / "models.md"
CALLS_LOG = RESULTS_DIR / "calls.jsonl"
RUNS_LOG = RESULTS_DIR / "runs.jsonl"

_PRICE_ROW = re.compile(r"^\| `([^`]+)` \| \$([\d.]+) \| \$([\d.]+) \|")


# ---------------------------------------------------------------------------
# 設定・単価・データ
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_prices() -> dict[str, tuple[Decimal, Decimal]]:
    """``models.md`` の表から (入力, 出力) の USD/1M tok を読む。"""

    prices: dict[str, tuple[Decimal, Decimal]] = {}
    for line in MODELS_MD.read_text(encoding="utf-8").splitlines():
        m = _PRICE_ROW.match(line)
        if m:
            prices[m.group(1)] = (Decimal(m.group(2)), Decimal(m.group(3)))
    return prices


def cost_usd(price: tuple[Decimal, Decimal], in_tok: int, out_tok: int) -> Decimal:
    return (Decimal(in_tok) * price[0] + Decimal(out_tok) * price[1]) / 1_000_000


def load_extract_cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    base = EVAL_DIR.parent / config["datasets"]["extract_cases_dir"]
    return [
        json.loads((base / n).read_text(encoding="utf-8"))
        for n in config["datasets"]["extract_case_files"]
    ]


def load_json(rel: str) -> dict[str, Any]:
    return json.loads((EVAL_DIR.parent / rel).read_text(encoding="utf-8"))


def clean_note(note: str) -> str:
    """正解データの note から採点者向けの注記（括弧・2文目以降）を落として要約にする。"""

    return re.sub(r"\(.*?\)", "", note).split("。")[0].strip()


def reason_from_text(text: str) -> str | None:
    return text.split("理由：", 1)[1].strip() if "理由：" in text else None


# ---------------------------------------------------------------------------
# 費用上限（全プロセス共通、calls.jsonl の累計から再開）
# ---------------------------------------------------------------------------


class BudgetExceededError(RuntimeError):
    """呼ぶと上限を超えうるため、呼ばなかった。"""


class LlmCallError(RuntimeError):
    """タイムアウト・HTTP エラーなど、呼び出し自体が失敗した。"""


class Budget:
    """呼ぶ前に最悪値を予約し、呼んだ後に実費で精算する（スレッド安全）。"""

    def __init__(self, cap: Decimal, spent: Decimal) -> None:
        self.cap = cap
        self.spent = spent
        self._reserved = Decimal("0")
        self._lock = threading.Lock()

    def reserve(self, worst: Decimal) -> None:
        with self._lock:
            if self.spent + self._reserved + worst > self.cap:
                raise BudgetExceededError(
                    f"budget cap ${self.cap} (spent ${self.spent:.4f}, worst ${worst:.4f})"
                )
            self._reserved += worst

    def settle(self, worst: Decimal, actual: Decimal) -> None:
        with self._lock:
            self._reserved -= worst
            self.spent += actual


_write_lock = threading.Lock()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with _write_lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# LLM 呼び出し（LlmFn プロトコルを満たす評価用クライアント）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalCompletion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class EvalLlm:
    """段階名 → モデルの表で呼び分ける。呼び出しごとの記録を ``calls`` に残す。"""

    def __init__(
        self,
        *,
        client: OpenAI,
        budget: Budget,
        prices: dict[str, tuple[Decimal, Decimal]],
        model_by_stage: dict[str, str],
        reasoning: str,
        config: dict[str, Any],
        unit_key: str,
    ) -> None:
        self._client = client
        self._budget = budget
        self._prices = prices
        self._model_by_stage = model_by_stage
        self._reasoning = reasoning
        self._config = config
        self._unit_key = unit_key
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        stage: str,
        messages: list[dict[str, object]],
        *,
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> EvalCompletion:
        key = stage.lower()
        model = self._model_by_stage[key]
        price = self._prices[model]
        max_tokens = int(self._config["max_tokens_by_stage"][key])
        # 思考を止められないモデル（GLM）と思考 low の変種は、本番の AD-2 と同じく上限を引き上げる
        if self._reasoning != "off" or model in self._config["thinking_uncontrollable"]:
            max_tokens = max(max_tokens, int(self._config["reasoning_min_max_tokens"]))

        chars = sum(len(m["content"]) for m in messages if isinstance(m.get("content"), str))
        worst = cost_usd(price, chars, max_tokens)  # 1文字=1トークンの保守的な見積り
        self._budget.reserve(worst)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self._config["temperature"],
        }
        if self._reasoning != "off":
            kwargs["reasoning_effort"] = self._reasoning
        else:
            # 思考 off の送り方はモデルごとに違う（2026-09-22 に実測。eval-config.json 参照）
            for prefix, extra in self._config["thinking_off_extra_body"].items():
                if model.startswith(prefix):
                    kwargs["extra_body"] = extra
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools

        row: dict[str, Any] = {"unit": self._unit_key, "stage": key, "model": model}
        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            self._budget.settle(worst, Decimal("0"))
            if isinstance(exc, APITimeoutError):
                err = "timeout"
            elif isinstance(exc, APIStatusError):
                err = f"http_{exc.status_code}: {str(exc.message)[:200]}"
            else:
                err = f"{type(exc).__name__}: {str(exc)[:200]}"
            row.update(latency_ms=int((time.monotonic() - started) * 1000), error=err, cost=0.0)
            self._record(row)
            raise LlmCallError(err) from None
        latency_ms = int((time.monotonic() - started) * 1000)

        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        content = getattr(message, "content", None)
        text = content if isinstance(content, str) else ""
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", None)
        out_tok = getattr(usage, "completion_tokens", None)
        estimated = in_tok is None or out_tok is None
        in_tok = int(in_tok) if in_tok is not None else math.ceil(chars / 2)
        out_tok = int(out_tok) if out_tok is not None else math.ceil(len(text) / 2)
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tok = getattr(details, "reasoning_tokens", None) or 0

        actual = cost_usd(price, in_tok, out_tok)
        self._budget.settle(worst, actual)
        row.update(
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            reasoning_tokens=int(reasoning_tok),
            usage_estimated=estimated,
            cost=float(actual),
            json_ok=_is_json_object(text),
            finish_reason=getattr(choices[0], "finish_reason", None) if choices else None,
            text=text,
        )
        self._record(row)
        return EvalCompletion(text=text, input_tokens=in_tok, output_tokens=out_tok, model=model)

    def _record(self, row: dict[str, Any]) -> None:
        self.calls.append(row)
        append_jsonl(CALLS_LOG, row)


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except json.JSONDecodeError:
        return False


# ---------------------------------------------------------------------------
# 入力の組み立て
# ---------------------------------------------------------------------------


def build_extract_input(case: dict[str, Any]) -> ExtractInput:
    return ExtractInput(
        goal_description=case["goal_description"],
        segments=[SegmentIn(**s) for s in case["segments"]],
        active_events=[],
    )


def build_support_sets(
    support_data: dict[str, Any], cases: list[dict[str, Any]]
) -> dict[str, tuple[SupportInput, list[str]]]:
    """裏付け検査の入力を2組作る（元の4件と、抽出データから決定的に作る20件）。

    派生の20件：正解の出来事10件それぞれについて、(a) 正しい根拠の区切りと組にした
    supported、(b) 別のケースの根拠の区切りと組にした unsupported。順番は固定の
    乱数（seed=0）で混ぜる。
    """

    orig_items = [
        SupportItem(
            event=ExtractedEvent(**c["event"]), segments=[SegmentIn(**s) for s in c["segments"]]
        )
        for c in support_data["cases"]
    ]
    orig_labels = [c["expected_result"] for c in support_data["cases"]]

    flat: list[tuple[dict[str, Any], SegmentIn]] = []
    for case in cases:
        seg_by_label = {s["label"]: s for s in case["segments"]}
        for e in case["expected_events"]:
            flat.append((e, SegmentIn(**seg_by_label[e["segment_ids"][0]])))

    def _item(e: dict[str, Any], own: SegmentIn, seg: SegmentIn) -> SupportItem:
        event = ExtractedEvent(
            kind=e["kind"],
            summary=clean_note(e["note"]),
            reason=reason_from_text(own.text),
            occurred_at=own.recorded_at,
            segment_ids=[seg.label],
            origin=e.get("origin"),
            supersedes_event_no=None,
            conflicts_with_event_no=None,
        )
        return SupportItem(event=event, segments=[seg])

    pairs: list[tuple[SupportItem, str]] = []
    n = len(flat)
    for i, (e, own) in enumerate(flat):
        pairs.append((_item(e, own, own), "supported"))
        other = flat[(i + n // 2) % n][1]  # 別ケースの根拠（件数配分上、必ず別ケースになる）
        pairs.append((_item(e, own, other), "unsupported"))
    random.Random(0).shuffle(pairs)
    derived = (SupportInput(items=[p[0] for p in pairs]), [p[1] for p in pairs])
    return {
        "support_orig": (SupportInput(items=orig_items), orig_labels),
        "support_derived": derived,
    }


def case_event_summaries(case: dict[str, Any]) -> list[EventSummary]:
    seg_by_label = {s["label"]: s for s in case["segments"]}
    return [
        EventSummary(
            event_no=i + 1,
            kind=e["kind"],
            summary=clean_note(e["note"]),
            reason=reason_from_text(seg_by_label[e["segment_ids"][0]]["text"]),
        )
        for i, e in enumerate(case["expected_events"])
    ]


# ---------------------------------------------------------------------------
# 採点（決定的なコードのみ）
# ---------------------------------------------------------------------------


def score_extraction(
    events: list[ExtractedEvent] | list[dict[str, Any]], case: dict[str, Any]
) -> dict[str, Any]:
    """kind が一致し、正解の根拠の区切り番号を1つ以上含めば一致（貪欲に1対1）。"""

    got = [
        (e.kind, set(e.segment_ids), e.origin)
        if isinstance(e, ExtractedEvent)
        else (e["kind"], set(e["segment_ids"]), e.get("origin"))
        for e in events
    ]
    used: set[int] = set()
    origin_ok = 0
    for exp in case["expected_events"]:
        for j, (kind, ids, origin) in enumerate(got):
            if j not in used and kind == exp["kind"] and ids & set(exp["segment_ids"]):
                used.add(j)
                origin_ok += int(origin == exp.get("origin"))
                break
    tp = len(used)
    fp = len(got) - tp
    fn = len(case["expected_events"]) - tp
    p, r, f1 = prf(tp, fp, fn)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": p,
        "recall": r,
        "f1": f1,
        "origin_ok": origin_ok,
    }


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """正解0件・抽出0件は満点、正解0件で抽出ありは精度0・F1 0とする。"""

    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    if tp + fn == 0 and fp > 0:
        f1 = 0.0
    return p, r, f1


def score_support(results: list[str], labels: list[str]) -> dict[str, Any]:
    n = len(labels)
    strict = sum(r == lab for r, lab in zip(results, labels, strict=True))
    false_supported = sum(
        r == "supported" and lab == "unsupported" for r, lab in zip(results, labels, strict=True)
    )
    false_rejected = sum(
        r != "supported" and lab == "supported" for r, lab in zip(results, labels, strict=True)
    )
    return {
        "n": n,
        "accuracy": strict / n,
        "false_supported": false_supported,
        "false_rejected": false_rejected,
        "partial": sum(r == "partial" for r in results),
    }


def score_assemble(
    output: AssembleOutput, raw_text: str | None, events: list[EventSummary]
) -> dict[str, Any]:
    """組み立ての決定的な採点（形式・根拠番号・網羅・見出しの配置・上限）。"""

    valid_nos = {e.event_no for e in events}
    raw: dict[str, Any] = {}
    if raw_text and _is_json_object(raw_text):
        raw = json.loads(raw_text)
    raw_sections = raw.get("sections") if isinstance(raw.get("sections"), dict) else {}
    json_ok = 1.0 if raw else 0.0
    headings_ok = 1.0 if raw and all(h in raw_sections for h in DIFF_HEADINGS) else 0.0

    checked = apply_mechanical_checks(
        output, headings=DIFF_HEADINGS, valid_event_nos=valid_nos, unsupported_event_nos=set()
    )
    sentences = [s for ss in output.sections.values() for s in ss]
    n_sent = len(sentences)
    invalid_refs = sum(1 for s in sentences for n in s.event_nos if n not in valid_nos)
    cited_rate = 1.0 - checked.no_evidence_count / n_sent if n_sent else (1.0 if raw else 0.0)
    coverage = len(checked.cited_event_nos & valid_nos) / len(valid_nos) if valid_nos else None

    placement_hits, placement_total = 0, 0
    target = {"decision": "decisions", "rejected_option": "rejected_options"}
    for e in events:
        heading = target.get(e.kind)
        if heading is None:
            continue
        placement_total += 1
        placement_hits += any(e.event_no in s.event_nos for s in output.sections.get(heading, []))
    placement = placement_hits / placement_total if placement_total else None

    limits_ok = float(
        bool(raw)
        and all(len(ss) <= 5 for ss in output.sections.values())
        and all(len(s.text) <= 300 for s in sentences)
        and bool(output.summary_for_mail)
    )
    parts = [json_ok, headings_ok, cited_rate, coverage, placement, limits_ok]
    parts = [x for x in parts if x is not None]
    return {
        "json_ok": json_ok,
        "headings_ok": headings_ok,
        "cited_rate": cited_rate,
        "coverage": coverage,
        "placement": placement,
        "limits_ok": limits_ok,
        "invalid_refs": invalid_refs,
        "sentences": n_sent,
        "assemble_score": sum(parts) / len(parts),
    }


# ---------------------------------------------------------------------------
# 実行単位
# ---------------------------------------------------------------------------


@dataclass
class Ctx:
    config: dict[str, Any]
    client: OpenAI
    budget: Budget
    prices: dict[str, tuple[Decimal, Decimal]]
    done: set[str]


def make_ctx(config: dict[str, Any]) -> Ctx:
    settings = get_settings()  # 鍵・接続先は設定ローダーがプログラム内で読む
    client = OpenAI(
        api_key=settings.api_key.get_secret_value(),
        base_url=str(settings.base_url),
        timeout=float(config["call_timeout_seconds"]),
        max_retries=0,  # 失敗（タイムアウト・400等）を隠さず数えるため再試行しない
    )
    spent = sum(Decimal(str(c.get("cost", 0))) for c in read_jsonl(CALLS_LOG))
    done = {r["key"] for r in read_jsonl(RUNS_LOG)}
    return Ctx(
        config, client, Budget(Decimal(str(config["cost_cap_usd"])), spent), load_prices(), done
    )


def run_unit(
    ctx: Ctx,
    *,
    key: str,
    meta: dict[str, Any],
    model_by_stage: dict[str, str],
    reasoning: str,
    body: Callable[[EvalLlm], dict[str, Any]],
) -> None:
    """1単位を実行して ``runs.jsonl`` に1行書く（既にあれば飛ばす）。"""

    if key in ctx.done:
        return
    llm = EvalLlm(
        client=ctx.client,
        budget=ctx.budget,
        prices=ctx.prices,
        model_by_stage=model_by_stage,
        reasoning=reasoning,
        config=ctx.config,
        unit_key=key,
    )
    error = None
    metrics: dict[str, Any] = {}
    try:
        metrics = body(llm)
    except BudgetExceededError as exc:
        print(f"[budget] {key}: {exc}")
        raise
    except LlmCallError as exc:
        error = str(exc)
    calls = llm.calls
    ok = [c for c in calls if "error" not in c]
    row = {
        "key": key,
        **meta,
        "reasoning": reasoning,
        "error": error,
        "n_calls": len(calls),
        "n_call_errors": len(calls) - len(ok),
        "call_errors": [c["error"] for c in calls if "error" in c],
        "json_ok_calls": sum(1 for c in ok if c.get("json_ok")),
        "latency_ms": sum(c["latency_ms"] for c in calls),
        "input_tokens": sum(c.get("input_tokens", 0) for c in ok),
        "output_tokens": sum(c.get("output_tokens", 0) for c in ok),
        "reasoning_tokens": sum(c.get("reasoning_tokens", 0) for c in ok),
        "cost_usd": sum(c.get("cost", 0.0) for c in calls),
        "metrics": metrics,
    }
    append_jsonl(RUNS_LOG, row)
    ctx.done.add(key)
    print(f"{key} err={error} cost=${row['cost_usd']:.5f} total=${ctx.budget.spent:.4f}")


def extract_body(case: dict[str, Any]) -> Callable[[EvalLlm], dict[str, Any]]:
    def body(llm: EvalLlm) -> dict[str, Any]:
        out = extract_events(build_extract_input(case), llm)
        m = score_extraction(out.events, case)
        m["parse_failed"] = not any(c.get("json_ok") for c in llm.calls)
        m["first_call_json_ok"] = bool(llm.calls and llm.calls[0].get("json_ok"))
        m["events"] = [e.model_dump(mode="json") for e in out.events]
        return m

    return body


def run_extract(ctx: Ctx, models: list[str], reasoning: str, phase: str = "extract") -> None:
    cases = load_extract_cases(ctx.config)
    reps = 1 if phase == "smoke" else int(ctx.config["repeats"])
    cases = cases[:1] if phase == "smoke" else cases

    def per_model(model: str) -> None:
        for rep in range(reps):
            for case in cases:
                key = f"{phase}|{reasoning}|{model}|{case['case_id']}|{rep}"
                meta = {"phase": phase, "label": model, "case_id": case["case_id"], "rep": rep}
                run_unit(
                    ctx,
                    key=key,
                    meta=meta,
                    model_by_stage={"extract": model},
                    reasoning=reasoning,
                    body=extract_body(case),
                )

    _parallel(per_model, models)


def _parallel(fn: Callable[[str], None], items: list[str]) -> None:
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        for fut in [pool.submit(fn, it) for it in items]:
            fut.result()


def cheapest(ctx: Ctx, n: int) -> list[str]:
    models = [c["model"] for c in ctx.config["candidates"]]
    return sorted(models, key=lambda m: ctx.prices[m][0] + ctx.prices[m][1])[:n]


def run_stage2(ctx: Ctx, models: list[str]) -> None:
    cases = load_extract_cases(ctx.config)
    support_sets = build_support_sets(
        load_json(ctx.config["datasets"]["support_cases_file"]), cases
    )
    judge_cases = load_json(ctx.config["datasets"]["judge_cases_file"])["cases"]

    def per_model(model: str) -> None:
        stages = {s: model for s in ("support", "assemble", "judge")}
        for rep in range(int(ctx.config["repeats"])):
            for set_id, (inp, labels) in support_sets.items():

                def sup(llm: EvalLlm, inp: SupportInput = inp, labels=labels) -> dict[str, Any]:
                    out = check_support(inp, llm)
                    m = score_support(out.results, labels)
                    m["count_mismatch_fallback"] = not any(c.get("json_ok") for c in llm.calls) or (
                        _results_len(llm.calls[-1].get("text", "")) != len(labels)
                    )
                    return m

                run_unit(
                    ctx,
                    key=f"support|off|{model}|{set_id}|{rep}",
                    meta={"phase": "support", "label": model, "case_id": set_id, "rep": rep},
                    model_by_stage=stages,
                    reasoning="off",
                    body=sup,
                )

            for case in cases:
                events = case_event_summaries(case)

                def asm(
                    llm: EvalLlm,
                    events: list[EventSummary] = events,
                    goal: str = case["goal_description"],
                ) -> dict[str, Any]:
                    inp = AssembleInput(
                        mode="diff",
                        goal_description=goal,
                        events=events,
                        new_event_nos=[e.event_no for e in events],
                    )
                    out = assemble_report(inp, llm)
                    return score_assemble(out, llm.calls[-1].get("text"), events)

                run_unit(
                    ctx,
                    key=f"assemble|off|{model}|{case['case_id']}|{rep}",
                    meta={
                        "phase": "assemble",
                        "label": model,
                        "case_id": case["case_id"],
                        "rep": rep,
                    },
                    model_by_stage=stages,
                    reasoning="off",
                    body=asm,
                )

            for jc in judge_cases:
                output = AssembleOutput(
                    sections={
                        h: [
                            Sentence(text=t, event_nos=[1], no_evidence=False)
                            for t in jc["sections"].get(h, [])
                        ]
                        for h in DIFF_HEADINGS
                    },
                    summary_for_mail="",
                )

                def jud(
                    llm: EvalLlm,
                    output: AssembleOutput = output,
                    expected: str = jc["expected_status"],
                ) -> dict[str, Any]:
                    verdict = judge_report(output, llm)
                    parsed = any(c.get("json_ok") for c in llm.calls)
                    return {
                        "expected": expected,
                        "status": verdict.status,
                        "correct": verdict.status == expected,
                        "parsed": parsed,
                    }

                run_unit(
                    ctx,
                    key=f"judge|off|{model}|{jc['case_id']}|{rep}",
                    meta={"phase": "judge", "label": model, "case_id": jc["case_id"], "rep": rep},
                    model_by_stage=stages,
                    reasoning="off",
                    body=jud,
                )

    _parallel(per_model, models)


def _results_len(text: str) -> int:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return -1
    res = parsed.get("results") if isinstance(parsed, dict) else None
    return len(res) if isinstance(res, list) else -1


# --- 段階3：構成 A / B / C ---------------------------------------------------


def config_models(letter: str, good: str, cheap: str) -> dict[str, str]:
    """A=全段階 good、B=抽出だけ good・他は cheap、C=全段階 cheap（不裏付けだけ good で再抽出）。"""

    if letter == "A":
        return {s: good for s in ("extract", "support", "assemble", "judge")}
    if letter == "B":
        return {"extract": good, "support": cheap, "assemble": cheap, "judge": cheap}
    return {s: cheap for s in ("extract", "support", "assemble", "judge")}


def pipeline_body(
    case: dict[str, Any], letter: str, good: str
) -> Callable[[EvalLlm], dict[str, Any]]:
    def body(llm: EvalLlm) -> dict[str, Any]:
        inp = build_extract_input(case)
        seg_by_label = {s.label: s for s in inp.segments}
        events = list(extract_events(inp, llm).events)

        def support_of(evs: list[ExtractedEvent]) -> list[str]:
            items = [
                SupportItem(event=e, segments=[seg_by_label[s] for s in e.segment_ids]) for e in evs
            ]
            return check_support(SupportInput(items=items), llm).results

        results = support_of(events)
        reextracted = 0
        if letter == "C" and "unsupported" in results:
            # 不裏付けの出来事だけ good モデルで抽出し直す：塊全体を good で抽出し、
            # 不裏付けの出来事と根拠の区切りが重なる出来事で置き換える（無ければ捨てる）。
            llm._model_by_stage["extract"] = good
            try:
                good_events = extract_events(inp, llm).events
            finally:
                llm._model_by_stage["extract"] = llm._model_by_stage["support"]
            taken: set[int] = set()
            kept = [(e, r) for e, r in zip(events, results, strict=True) if r != "unsupported"]
            replacements: list[ExtractedEvent] = []
            for e, r in zip(events, results, strict=True):
                if r != "unsupported":
                    continue
                reextracted += 1
                for j, g in enumerate(good_events):
                    if j not in taken and set(g.segment_ids) & set(e.segment_ids):
                        taken.add(j)
                        replacements.append(g)
                        break
            new_results = support_of(replacements) if replacements else []
            pairs = kept + list(zip(replacements, new_results, strict=True))
            events = [p[0] for p in pairs]
            results = [p[1] for p in pairs]

        ext = score_extraction(events, case)
        summaries = [
            EventSummary(event_no=i + 1, kind=e.kind, summary=e.summary, reason=e.reason)
            for i, e in enumerate(events)
        ]
        unsupported_nos = {i + 1 for i, r in enumerate(results) if r == "unsupported"}
        aj = assemble_and_judge(
            AssembleInput(
                mode="diff",
                goal_description=case["goal_description"],
                events=summaries,
                new_event_nos=[s.event_no for s in summaries],
            ),
            llm,
            headings=DIFF_HEADINGS,
            valid_event_nos={s.event_no for s in summaries},
            unsupported_event_nos=unsupported_nos,
            allow_regeneration=True,
        )
        asm_calls = [c for c in llm.calls if c["stage"] == "assemble"]
        asm = score_assemble(aj.output, asm_calls[-1].get("text") if asm_calls else None, summaries)
        return {
            **{k: ext[k] for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
            "n_events": len(events),
            "unsupported": len(unsupported_nos),
            "partial": sum(r == "partial" for r in results),
            "reextracted": reextracted,
            "assemble_score": asm["assemble_score"],
            "no_evidence_sentences": aj.check.no_evidence_count,
            "judge_pass": aj.judge_status == "pass",
            "regenerated": len(asm_calls) > 1,
        }

    return body


def run_configs(ctx: Ctx, good: str, cheap: str) -> None:
    cases = load_extract_cases(ctx.config)

    def per_config(letter: str) -> None:
        for rep in range(int(ctx.config["repeats"])):
            for case in cases:
                key = f"config|{letter}|{good}|{cheap}|{case['case_id']}|{rep}"
                meta = {
                    "phase": "config",
                    "label": f"{letter} ({good.split('/')[-1]} / {cheap.split('/')[-1]})",
                    "case_id": case["case_id"],
                    "rep": rep,
                    "good": good,
                    "cheap": cheap,
                }
                run_unit(
                    ctx,
                    key=key,
                    meta=meta,
                    model_by_stage=config_models(letter, good, cheap),
                    reasoning="off",
                    body=pipeline_body(case, letter, good),
                )

    _parallel(per_config, ["A", "B", "C"])


# ---------------------------------------------------------------------------
# 集計・出力（results.md / results.csv / cost_vs_quality.svg）
# ---------------------------------------------------------------------------


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, math.ceil(0.95 * len(xs)) - 1)]


def _calls_by_unit() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for c in read_jsonl(CALLS_LOG):
        out.setdefault(c["unit"], []).append(c)
    return out


def summarize(runs: list[dict[str, Any]], calls: dict[str, list[dict[str, Any]]]) -> dict:
    """(phase, reasoning, label) ごとにまとめる。"""

    cases = {c["case_id"]: c for c in load_extract_cases(load_config())}

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((r["phase"], r["reasoning"], r["label"]), []).append(r)
    out = {}
    for (phase, reasoning, label), rs in groups.items():
        cs = [c for r in rs for c in calls.get(r["key"], [])]
        ok_calls = [c for c in cs if "error" not in c]
        good = [r for r in rs if not r["error"]]
        m = [r["metrics"] for r in good]
        s: dict[str, Any] = {
            "phase": phase,
            "reasoning": reasoning,
            "label": label,
            "units": len(rs),
            "unit_errors": len(rs) - len(good),
            "calls": len(cs),
            "call_errors": sorted({c["error"][:80] for c in cs if "error" in c}),
            "n_call_errors": len(cs) - len(ok_calls),
            "json_valid_rate": _mean([float(bool(c.get("json_ok"))) for c in ok_calls]),
            "lat_mean_s": (_mean([c["latency_ms"] for c in ok_calls]) or 0) / 1000,
            "lat_p95_s": (_p95([c["latency_ms"] for c in ok_calls]) or 0) / 1000,
            "cost_total": sum(r["cost_usd"] for r in rs),
            "cost_per_unit": sum(r["cost_usd"] for r in rs) / len(rs),
            "reasoning_tokens": sum(r["reasoning_tokens"] for r in rs),
            "out_tokens_mean": _mean([c.get("output_tokens", 0) for c in ok_calls]),
        }
        if phase in ("extract", "config") and m:
            tp, fp, fn = (sum(x[k] for x in m) for k in ("tp", "fp", "fn"))
            s["precision"], s["recall"], s["f1"] = prf(tp, fp, fn)
            s["tp"], s["fp"], s["fn"] = tp, fp, fn
            per_rep = []
            for rep in sorted({r["rep"] for r in good}):
                mr = [r["metrics"] for r in good if r["rep"] == rep]
                per_rep.append(prf(*(sum(x[k] for x in mr) for k in ("tp", "fp", "fn")))[2])
            s["f1_rep_min"], s["f1_rep_max"] = min(per_rep), max(per_rep)
        if phase == "extract" and m:
            fixed = [
                replay_fence_stripped(cases[r["case_id"]], calls.get(r["key"], [])) for r in good
            ]
            s["f1_fence_fixed"] = prf(*(sum(x[k] for x in fixed) for k in ("tp", "fp", "fn")))[2]
            s["parse_failed"] = sum(bool(x["parse_failed"]) for x in m)
            s["origin_acc"] = (sum(x["origin_ok"] for x in m) / s["tp"]) if s["tp"] else None
            case6 = [x for r, x in zip(good, m, strict=True) if r["case_id"] == "case_6_injection"]
            s["inj_llm_flag"] = _mean(
                [
                    _llm_flagged(calls.get(r["key"], []), "S6-8")
                    for r in good
                    if r["case_id"] == "case_6_injection"
                ]
            )
            s["case6_f1"] = _mean([x["f1"] for x in case6])
            case5 = [x for r, x in zip(good, m, strict=True) if r["case_id"].startswith("case_5")]
            s["worklog_fp"] = _mean([x["fp"] for x in case5])
        if phase == "support" and m:
            for set_id in ("support_orig", "support_derived"):
                ms = [r["metrics"] for r in good if r["case_id"] == set_id]
                s[f"{set_id}_acc"] = _mean([x["accuracy"] for x in ms])
            s["false_supported"] = sum(x["false_supported"] for x in m)
            s["false_rejected"] = sum(x["false_rejected"] for x in m)
            s["partial"] = sum(x["partial"] for x in m)
            s["items"] = sum(x["n"] for x in m)
            s["fallbacks"] = sum(bool(x["count_mismatch_fallback"]) for x in m)
            s["support_score"] = 1 - (s["false_supported"] + s["false_rejected"]) / s["items"]
        if phase == "assemble" and m:
            for k in (
                "assemble_score",
                "json_ok",
                "headings_ok",
                "cited_rate",
                "coverage",
                "placement",
                "limits_ok",
            ):
                s[k] = _mean([x[k] for x in m])
            s["invalid_refs"] = sum(x["invalid_refs"] for x in m)
        if phase == "judge" and m:
            s["judge_acc"] = _mean([float(x["correct"]) for x in m])
            s["false_pass"] = sum(x["status"] == "pass" and x["expected"] == "fail" for x in m)
            s["false_fail"] = sum(x["status"] == "fail" and x["expected"] == "pass" for x in m)
            s["unparsed"] = sum(not x["parsed"] for x in m)
        if phase == "config" and m:
            s["assemble_score"] = _mean([x["assemble_score"] for x in m])
            s["judge_pass"] = _mean([float(x["judge_pass"]) for x in m])
            s["unsupported"] = sum(x["unsupported"] for x in m)
            s["reextracted"] = sum(x["reextracted"] for x in m)
            s["regenerated"] = sum(bool(x["regenerated"]) for x in m)
            s["pipeline_score"] = _mean([s["f1"], s["assemble_score"], s["judge_pass"]])
            s["cost_per_case"] = s["cost_per_unit"]
            s["good"], s["cheap"] = rs[0].get("good"), rs[0].get("cheap")
        out[(phase, reasoning, label)] = s
    return out


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def replay_fence_stripped(case: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """参考値：記録済みの応答からコードフェンス（```json）を外して本番の後処理にかけ直す（費用0）。"""

    texts = [_FENCE.sub(r"\1", c["text"]) for c in calls if "text" in c]
    text = next((t for t in texts if _is_json_object(t)), "")
    replay = extract_events(
        build_extract_input(case), lambda *a, **k: EvalCompletion(text, 0, 0, "")
    )
    return score_extraction(replay.events, case)


def _llm_flagged(calls: list[dict[str, Any]], label: str) -> float | None:
    """LLM 自身が誘導の区切りを報告したか（コードの規則による補完とは分けて見る）。"""

    for c in reversed(calls):
        if c.get("json_ok"):
            ids = json.loads(c["text"]).get("suspected_injection_segment_ids") or []
            return float(label in ids)
    return None


def _f(v: Any, nd: int = 3) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write_csv(runs: list[dict[str, Any]], path: Path) -> None:
    metric_keys = sorted(
        {k for r in runs for k, v in r["metrics"].items() if not isinstance(v, list | dict)}
    )
    base = [
        "phase",
        "reasoning",
        "label",
        "case_id",
        "rep",
        "error",
        "n_calls",
        "n_call_errors",
        "json_ok_calls",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost_usd",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(base + metric_keys)
        for r in runs:
            w.writerow([r.get(k) for k in base] + [r["metrics"].get(k) for k in metric_keys])


def render_svg(summ: dict, path: Path) -> None:
    """費用（対数軸）× 品質の散布図を2枚並べた SVG（matplotlib 不在のため手書き）。"""

    ext = [s for (p, _, _), s in summ.items() if p == "extract"]
    cfg = [s for (p, _, _), s in summ.items() if p == "config"]
    panels = [
        ("段階1 抽出：F1 × 1ケースあたり費用", ext, "f1", "cost_per_unit"),
        ("段階3 構成A/B/C：総合スコア × 1ケースあたり費用", cfg, "pipeline_score", "cost_per_case"),
    ]
    pw, ph, m = 520, 400, 60
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {pw * 2} {ph + 40}" '
        'font-family="sans-serif" font-size="11">',
        "<style>.bg{fill:#fcfcfb}.ink{fill:#0b0b0b}.ink2{fill:#52514e}.grid{stroke:#e4e3de}"
        ".axis{stroke:#8a8983}.s1{fill:#2a78d6}.s2{fill:#eb6834}.s3{fill:#1baf7a}"
        "@media (prefers-color-scheme: dark){.bg{fill:#1a1a19}.ink{fill:#fff}.ink2{fill:#c3c2b7}"
        ".grid{stroke:#34332f}.s1{fill:#3987e5}.s2{fill:#d95926}.s3{fill:#199e70}}</style>",
        f'<rect class="bg" width="{pw * 2}" height="{ph + 40}"/>',
    ]
    for i, (title, pts, yk, xk) in enumerate(panels):
        ox = i * pw
        pts = [p for p in pts if p.get(yk) is not None and p.get(xk)]
        parts.append(f'<text class="ink" x="{ox + m}" y="24" font-size="13">{title}</text>')
        if not pts:
            parts.append(f'<text class="ink2" x="{ox + m}" y="60">（データなし）</text>')
            continue
        xs = [math.log10(p[xk]) for p in pts]
        lo, hi = math.floor(min(xs)), math.ceil(max(xs))
        hi = hi if hi > lo else lo + 1

        def px(v: float, ox: int = ox, lo: int = lo, hi: int = hi) -> float:
            return ox + m + (math.log10(v) - lo) / (hi - lo) * (pw - 2 * m)

        def py(v: float) -> float:
            return ph - m + 20 - v * (ph - 2 * m)

        for t in range(lo, hi + 1):
            x = px(10**t)
            parts.append(
                f'<line class="grid" x1="{x:.1f}" y1="{py(0):.1f}" x2="{x:.1f}" '
                f'y2="{py(1):.1f}"/><text class="ink2" x="{x:.1f}" y="{py(0) + 16:.1f}" '
                f'text-anchor="middle">${10**t:g}</text>'
            )
        for q in (0, 0.25, 0.5, 0.75, 1.0):
            parts.append(
                f'<line class="grid" x1="{px(10**lo):.1f}" y1="{py(q):.1f}" '
                f'x2="{px(10**hi):.1f}" y2="{py(q):.1f}"/><text class="ink2" '
                f'x="{ox + m - 6}" y="{py(q) + 4:.1f}" text-anchor="end">{q:g}</text>'
            )
        parts.append(
            f'<text class="ink2" x="{ox + pw / 2}" y="{ph + 30}" text-anchor="middle">'
            "1ケースあたり費用 USD（対数軸）</text>"
        )
        placed: list[tuple[float, float, float]] = []  # 置いたラベルの (x, y, 幅)
        for p in sorted(pts, key=lambda q: -q[yk]):
            cls = "s2" if p["reasoning"] == "low" else ("s3" if p["phase"] == "config" else "s1")
            name = p["label"].split("/")[-1] + (" (low)" if p["reasoning"] == "low" else "")
            if p["phase"] == "config":
                name = f"構成{p['label']}"
                name = name.replace("qwen3.7-flash", "qwen").replace("gpt-5.4-nano", "nano")
            x, y = px(p[xk]), py(p[yk])
            # ラベルの重なりを避けるため、既に置いたラベルと重なれば下へずらす
            lw, ly = len(name) * 7.0, y - 6
            while any(
                abs(ly - py_) < 13 and x + 8 < px_ + pw_ and px_ < x + 8 + lw
                for px_, py_, pw_ in placed
            ):
                ly += 13
            placed.append((x + 8, ly, lw))
            parts.append(
                f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="5" '
                f'stroke="#fcfcfb" stroke-width="2"><title>{name}: {p[yk]:.3f} / '
                f'${p[xk]:.5f}</title></circle><text class="ink" x="{x + 8:.1f}" '
                f'y="{ly:.1f}">{name}</text>'
            )
    legend_y = 46
    parts.append(
        f'<circle class="s1" cx="{pw * 2 - 300}" cy="{legend_y - 4}" r="5"/>'
        f'<text class="ink2" x="{pw * 2 - 290}" y="{legend_y}">思考 off</text>'
        f'<circle class="s2" cx="{pw * 2 - 220}" cy="{legend_y - 4}" r="5"/>'
        f'<text class="ink2" x="{pw * 2 - 210}" y="{legend_y}">思考 low</text>'
        f'<circle class="s3" cx="{pw * 2 - 140}" cy="{legend_y - 4}" r="5"/>'
        f'<text class="ink2" x="{pw * 2 - 130}" y="{legend_y}">構成A/B/C</text>'
    )
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_markdown(summ: dict, runs: list[dict[str, Any]], path: Path) -> None:
    total = sum(c.get("cost", 0.0) for c in read_jsonl(CALLS_LOG))
    lines = [
        "# モデル比較の評価結果",
        "",
        f"- 累計の推定費用：**${total:.4f}**（上限 $5.00、トークン数 × `models.md` の単価）",
        "- 条件：temperature 0、思考 off（qwen は `enable_thinking=false`、deepseek は "
        "`thinking={type:disabled}`。GLM は止められず出力上限16000で実行）、1呼び出し120秒、"
        "再試行なし、各3回。採点はすべてコード（LLM 不使用）。",
        "",
    ]

    def rows(phase: str) -> list[dict[str, Any]]:
        return sorted(
            [s for (p, _, _), s in summ.items() if p == phase],
            key=lambda s: (s["reasoning"], s["label"]),
        )

    common = "| 失敗呼出 | JSON有効率 | 平均遅延s | p95遅延s | 費用/件 USD | 費用計 USD |"
    common_sep = "|---|---|---|---|---|---|"

    def common_cells(s: dict[str, Any]) -> str:
        return (
            f"| {s['n_call_errors']} | {_f(s['json_valid_rate'])} | {s['lat_mean_s']:.1f} | "
            f"{s['lat_p95_s']:.1f} | {s['cost_per_unit']:.5f} | {s['cost_total']:.4f} |"
        )

    lines += [
        "## 段階1：抽出（6モデル × 6ケース × 3回）",
        "",
        "一致＝kind が一致し、正解の根拠の区切り番号を含む。P/R/F1 は全ケース合算（micro）、"
        "タイムアウト等で完了しなかった単位は除く（完了/全 を参照）。参考F1(```除去) は記録済みの"
        "応答からコードフェンスを外して本番の後処理にかけ直した値（本番コードは除去しない）。",
        "",
        "| モデル | 思考 | P | R | F1 | F1(回ごと最小–最大) | 出どころ正解率 "
        "| 作業ログのみ(case5)の誤抽出/回 | 誘導S6-8をLLMが報告 | 解析失敗 | 完了/全 "
        "| 参考F1(```除去) " + common,
        "|---|---|---|---|---|---|---|---|---|---|---|---" + common_sep,
    ]
    for s in rows("extract"):
        lines.append(
            f"| `{s['label']}` | {s['reasoning']} | {_f(s.get('precision'))} | "
            f"{_f(s.get('recall'))} | **{_f(s.get('f1'))}** | {_f(s.get('f1_rep_min'))}–"
            f"{_f(s.get('f1_rep_max'))} | {_f(s.get('origin_acc'))} | {_f(s.get('worklog_fp'), 2)} "
            f"| {_f(s.get('inj_llm_flag'), 2)} | {s.get('parse_failed', '-')} | "
            f"{s['units'] - s['unit_errors']}/{s['units']} | {_f(s.get('f1_fence_fixed'))} "
            + common_cells(s)
        )
    lines += [
        "",
        "## 段階2：裏付け検査・組み立て・Judge（最安3モデル × 3回）",
        "",
        "### 裏付け検査（support）",
        "",
        "元の4件＋抽出データから作った20件（正しい根拠10件＝supported、別ケースの根拠10件"
        "＝unsupported）。スコア＝1−(誤supported＋誤棄却)/件数（partial は「supported で"
        "ない」扱い）。",
        "",
        "| モデル | スコア | 厳密一致(元4件) | 厳密一致(派生20件) | 誤supported | 誤棄却 "
        "| partial | 件数不一致で全unsupported " + common,
        "|---|---|---|---|---|---|---|---" + common_sep,
    ]
    for s in rows("support"):
        lines.append(
            f"| `{s['label']}` | **{_f(s.get('support_score'))}** | "
            f"{_f(s.get('support_orig_acc'))} | {_f(s.get('support_derived_acc'))} | "
            f"{s.get('false_supported')} | {s.get('false_rejected')} | {s.get('partial')} | "
            f"{s.get('fallbacks')} " + common_cells(s)
        )
    lines += [
        "",
        "### 組み立て（assemble）",
        "",
        "入力＝各ケースの正解の出来事。スコア＝JSON・5見出し・根拠番号付きの文の率・"
        "出来事の網羅率・決定/却下の見出し配置・上限（5文/300字/メール要約）の平均。",
        "",
        "| モデル | スコア | JSON | 5見出し | 根拠付き率 | 網羅率 | 配置 | 上限 "
        "| 無効番号 " + common,
        "|---|---|---|---|---|---|---|---|---" + common_sep,
    ]
    for s in rows("assemble"):
        lines.append(
            f"| `{s['label']}` | **{_f(s.get('assemble_score'))}** | {_f(s.get('json_ok'))} | "
            f"{_f(s.get('headings_ok'))} | {_f(s.get('cited_rate'))} | {_f(s.get('coverage'))} | "
            f"{_f(s.get('placement'))} | {_f(s.get('limits_ok'))} | {s.get('invalid_refs')} "
            + common_cells(s)
        )
    lines += [
        "",
        "### Judge",
        "",
        "`eval/datasets/judge_cases.json` の8件（合格4・不合格4、正解ラベルは人手で付与）。",
        "",
        "| モデル | 正解率 | 誤合格 | 誤不合格 | 解釈不能 " + common,
        "|---|---|---|---|---" + common_sep,
    ]
    for s in rows("judge"):
        lines.append(
            f"| `{s['label']}` | **{_f(s.get('judge_acc'))}** | {s.get('false_pass')} | "
            f"{s.get('false_fail')} | {s.get('unparsed')} " + common_cells(s)
        )
    cfg = rows("config")
    if cfg:
        lines += [
            "",
            "## 段階3：構成 A / B / C（6ケース × 3回、本番と同じ流れ）",
            "",
            "- 構成名の括弧内は (good / cheap)。",
            "- A：全段階 good。B：抽出だけ good、裏付け・組み立て・Judge は cheap。"
            "C：全段階 cheap、裏付け検査で unsupported の出来事だけ good で再抽出して置換。",
            "- 総合スコア＝(抽出F1 ＋ 組み立てスコア ＋ Judge合格率)/3。"
            "Judge 不合格時は本番同様1回だけ組み立て直す。",
            "",
            "| 構成 | 抽出P | 抽出R | 抽出F1 | unsupported件 | 再抽出件 | 組み立て "
            "| Judge合格率 | 組み直し回数 | 総合 " + common,
            "|---|---|---|---|---|---|---|---|---|---" + common_sep,
        ]
        for s in cfg:
            lines.append(
                f"| {s['label']} | {_f(s.get('precision'))} | {_f(s.get('recall'))} | "
                f"{_f(s.get('f1'))} | {s.get('unsupported')} | {s.get('reextracted')} | "
                f"{_f(s.get('assemble_score'))} | {_f(s.get('judge_pass'))} | "
                f"{s.get('regenerated')} | **{_f(s.get('pipeline_score'))}** " + common_cells(s)
            )
    errs = [r for r in runs if r["error"] or r["n_call_errors"]]
    lines += ["", "## 失敗（タイムアウト・HTTP エラー）", ""]
    if errs:
        for r in errs:
            lines.append(f"- `{r['key']}`：{'; '.join(r['call_errors'])[:200]}")
    else:
        lines.append("- なし")
    lines += [
        "",
        "図：`cost_vs_quality.svg`、単位ごとの生データ：`results.csv`、"
        "呼び出しごとの記録：`calls.jsonl`。",
        "",
    ]
    rec = RESULTS_DIR / "recommendations.md"
    if rec.exists():
        lines += [rec.read_text(encoding="utf-8")]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def report() -> None:
    runs = read_jsonl(RUNS_LOG)
    summ = summarize(runs, _calls_by_unit())
    write_csv(runs, RESULTS_DIR / "results.csv")
    write_markdown(summ, runs, RESULTS_DIR / "results.md")
    render_svg(summ, RESULTS_DIR / "cost_vs_quality.svg")
    for (p, rz, lab), s in sorted(summ.items()):
        keys = ("f1", "support_score", "assemble_score", "judge_acc", "pipeline_score")
        q = next((s[k] for k in keys if s.get(k) is not None), None)
        print(
            f"{p:9} {rz:4} {lab:32} q={_f(q)} cost={s['cost_total']:.4f} err={s['n_call_errors']}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["smoke", "extract", "stage2", "configs", "report"])
    ap.add_argument("--models", help="カンマ区切り（既定は候補6つ）")
    ap.add_argument("--reasoning", default="off", choices=["off", "low"])
    ap.add_argument("--good")
    ap.add_argument("--cheap")
    args = ap.parse_args()

    config = load_config()
    if args.command == "report":
        report()
        return
    ctx = make_ctx(config)
    models = args.models.split(",") if args.models else [c["model"] for c in config["candidates"]]
    try:
        if args.command in ("smoke", "extract"):
            run_extract(ctx, models, args.reasoning, phase=args.command)
        elif args.command == "stage2":
            run_stage2(ctx, cheapest(ctx, 3) if not args.models else models)
        elif args.command == "configs":
            run_configs(ctx, args.good, args.cheap)
    except BudgetExceededError:
        print("費用上限に達するため中止しました。")
    print(f"累計推定費用: ${ctx.budget.spent:.4f}")
    report()


if __name__ == "__main__":
    main()
