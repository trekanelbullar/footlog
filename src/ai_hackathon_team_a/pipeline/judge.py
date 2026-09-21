"""形式の Judge（設計書 §5.3 (10)）。

不合格なら1回だけ組み立てをやり直し、やり直した後に機械の検査をかけ直す。
組ごとの入力しか渡さない（I1）。
"""

import json
from dataclasses import dataclass
from typing import Literal

from ai_hackathon_team_a.pipeline.assemble import assemble_report
from ai_hackathon_team_a.pipeline.checks import MechanicalCheckResult, apply_mechanical_checks
from ai_hackathon_team_a.pipeline.contracts import AssembleInput, AssembleOutput, LlmFn
from ai_hackathon_team_a.prompts import get_prompts

JudgeStatus = Literal["pass", "fail"]


@dataclass(frozen=True)
class JudgeVerdict:
    """Judge の判定。"""

    status: JudgeStatus
    notes: list[str]


def judge_report(output: AssembleOutput, llm: LlmFn) -> JudgeVerdict:
    """詳細設計書 4.2 のルーブリックで合否を判定させる。読めなければ不合格。"""

    messages = _build_messages(output)
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


def _build_messages(output: AssembleOutput) -> list[dict[str, object]]:
    system = get_prompts()["judge"]
    lines: list[str] = []
    for heading, sentences in output.sections.items():
        lines.append(f"## {heading}")
        lines.extend(f"- {sentence.text}" for sentence in sentences)
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
) -> AssembleAndJudgeResult:
    """設計書 §5.3 (9)(10) の一連（組み立て・機械の検査・Judge・1回だけのやり直し）。

    やり直しのときは、Judge の指摘（``notes``）を組み立てのプロンプトに添える
    （詳細設計書 4.2。契約の型は変えず、``assemble_report`` の任意の引数で渡す）。
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
        verdict = judge_report(assembled, llm)
        return assembled, checked, verdict

    assembled, checked, verdict = _run_once()
    if verdict.status == "fail":
        assembled, checked, verdict = _run_once(feedback=verdict.notes)

    judge_status: Literal["pass", "flagged"] = "pass" if verdict.status == "pass" else "flagged"
    return AssembleAndJudgeResult(output=assembled, check=checked, judge_status=judge_status)
