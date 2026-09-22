"""形式の Judge（設計書 §5.3 (10)）。

不合格なら1回だけ組み立てをやり直し、やり直した後に機械の検査をかけ直す。
組ごとの入力しか渡さない（I1）。ただし手動実行では、やり直しをせずに
``judge_status = flagged`` で終える（AD-7）。
"""

import json
from dataclasses import dataclass
from typing import Literal

from ai_hackathon_team_a.pipeline.assemble import assemble_report
from ai_hackathon_team_a.pipeline.checks import MechanicalCheckResult, apply_mechanical_checks
from ai_hackathon_team_a.pipeline.contracts import AssembleInput, AssembleOutput, LlmFn
from ai_hackathon_team_a.pipeline.render import (
    _UNRESOLVED_REASON_HEADINGS,
    _UNRESOLVED_REASON_SUFFIX,
)
from ai_hackathon_team_a.prompts import get_prompts

JudgeStatus = Literal["pass", "fail"]


@dataclass(frozen=True)
class JudgeVerdict:
    """Judge の判定。"""

    status: JudgeStatus
    notes: list[str]


def judge_report(
    output: AssembleOutput,
    llm: LlmFn,
    *,
    unresolved_reason_event_nos: frozenset[int] = frozenset(),
) -> JudgeVerdict:
    """詳細設計書 4.2 のルーブリックで合否を判定させる。読めなければ不合格。

    ``unresolved_reason_event_nos``：理由が空の決定・却下案の番号。表示（render）と同じ
    「理由は未確認」の印を Judge にも見せ、問い合わせ中の項目で不合格にさせない
    （2026-09-22、印が無いため版18以降がすべて flagged になっていた）。
    """

    messages = _build_messages(output, unresolved_reason_event_nos)
    result = llm("JUDGE", messages, json_mode=True)
    text = getattr(result, "text", None)
    verdict = _parse(text) if isinstance(text, str) else None
    return verdict or JudgeVerdict(status="fail", notes=["Judge の応答を解釈できませんでした。"])


def _parse(text: str) -> JudgeVerdict | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status not in ("pass", "fail"):
        return None
    notes = data.get("notes")
    notes = [str(n) for n in notes] if isinstance(notes, list) else []
    return JudgeVerdict(status=status, notes=notes)


def _build_messages(
    output: AssembleOutput, unresolved_reason_event_nos: frozenset[int] = frozenset()
) -> list[dict[str, object]]:
    system = get_prompts()["judge"]
    lines: list[str] = []
    for heading, sentences in output.sections.items():
        lines.append(f"## {heading}")
        for sentence in sentences:
            marker = (
                f" {_UNRESOLVED_REASON_SUFFIX}"
                if heading in _UNRESOLVED_REASON_HEADINGS
                and unresolved_reason_event_nos.intersection(sentence.event_nos)
                else ""
            )
            lines.append(f"- {sentence.text}{marker}")
    user = "\n".join(lines) or "（本文なし）"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


@dataclass(frozen=True)
class AssembleAndJudgeResult:
    """組み立て→機械の検査→Judge（→ 不合格なら1回だけやり直し）の結果。"""

    output: AssembleOutput
    check: MechanicalCheckResult
    judge_status: Literal["pass", "flagged"]


def assemble_and_judge(
    inp: AssembleInput,
    llm: LlmFn,
    *,
    headings: tuple[str, ...],
    valid_event_nos: set[int],
    unsupported_event_nos: set[int],
    allow_regeneration: bool = True,
    unresolved_reason_event_nos: frozenset[int] = frozenset(),
) -> AssembleAndJudgeResult:
    """設計書 §5.3 (9)(10) の一連（組み立て・機械の検査・Judge・1回だけのやり直し）。

    やり直しのときは、Judge の指摘（``notes``）を組み立てのプロンプトに添える
    （詳細設計書 4.2。契約の型は変えず、``assemble_report`` の任意の引数で渡す）。

    ``allow_regeneration=False``（手動実行、AD-7）のときは、Judge が不合格でも
    組み立てをやり直さず、``judge_status = flagged`` で終える。Judge 自体は
    どちらでも1回は呼ぶ（機械の検査は必ずかける）。
    """

    def _run_once(
        feedback: list[str] | None = None,
    ) -> tuple[AssembleOutput, MechanicalCheckResult, JudgeVerdict]:
        assembled = assemble_report(inp, llm, feedback=feedback)
        checked = apply_mechanical_checks(
            assembled,
            headings=headings,
            valid_event_nos=valid_event_nos,
            unsupported_event_nos=unsupported_event_nos,
        )
        verdict = judge_report(
            assembled, llm, unresolved_reason_event_nos=unresolved_reason_event_nos
        )
        return assembled, checked, verdict

    assembled, checked, verdict = _run_once()
    if verdict.status == "fail" and allow_regeneration:
        assembled, checked, verdict = _run_once(feedback=verdict.notes)

    judge_status: Literal["pass", "flagged"] = "pass" if verdict.status == "pass" else "flagged"
    return AssembleAndJudgeResult(output=assembled, check=checked, judge_status=judge_status)
