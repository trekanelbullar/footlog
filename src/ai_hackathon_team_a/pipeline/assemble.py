"""レポートの組み立て（設計書 §5.3 (9)）。

LLM には構造化 JSON を返させ、コードで ``AssembleOutput`` に変換する。番号の
検査・「根拠なし」付け・文字数の確認は ``pipeline/checks.py`` の機械の検査で行う
（ここではまだ行わない）。
"""

import json

from ai_hackathon_team_a.pipeline.contracts import (
    BASELINE_HEADINGS,
    DIFF_HEADINGS,
    AssembleInput,
    AssembleOutput,
    LlmFn,
    Sentence,
)
from ai_hackathon_team_a.prompts import get_prompts, render

_HEADINGS_BY_MODE: dict[str, tuple[str, ...]] = {
    "diff": DIFF_HEADINGS,
    "baseline": BASELINE_HEADINGS,
}
_TEMPLATE_BY_MODE: dict[str, str] = {"diff": "assemble_diff", "baseline": "assemble_baseline"}


def assemble_report(
    inp: AssembleInput, llm: LlmFn, *, feedback: list[str] | None = None
) -> AssembleOutput:
    """契約（入出力の型）は変えない（設計書 §6.4）。

    ``feedback`` は、Judge が不合格にしたときの指摘（やり直しのときだけ渡す）。任意の
    キーワード引数なので、``assemble_report(inp, llm)`` の呼び方はそのまま使える。
    """

    messages = _build_messages(inp, feedback=feedback)
    result = llm("ASSEMBLE", messages, json_mode=True)
    text = getattr(result, "text", None)
    parsed = _parse(text) if isinstance(text, str) else None
    if parsed is None:
        return AssembleOutput(sections={}, summary_for_mail="")

    headings = _HEADINGS_BY_MODE[inp.mode]
    raw_sections = parsed.get("sections")
    raw_sections = raw_sections if isinstance(raw_sections, dict) else {}

    sections: dict[str, list[Sentence]] = {
        heading: _parse_sentences(raw_sections.get(heading)) for heading in headings
    }

    summary = parsed.get("summary_for_mail")
    summary_for_mail = summary.strip() if isinstance(summary, str) else ""

    return AssembleOutput(sections=sections, summary_for_mail=summary_for_mail)


def _parse_sentences(raw_sentences: object) -> list[Sentence]:
    if not isinstance(raw_sentences, list):
        return []
    sentences: list[Sentence] = []
    for raw_sentence in raw_sentences:
        if not isinstance(raw_sentence, dict):
            continue
        text_value = raw_sentence.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            continue
        raw_nos = raw_sentence.get("event_nos") or []
        event_nos = [n for n in raw_nos if isinstance(n, int)] if isinstance(raw_nos, list) else []
        sentences.append(Sentence(text=text_value.strip(), event_nos=event_nos, no_evidence=False))
    return sentences


def _parse(text: str) -> dict | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_messages(
    inp: AssembleInput, *, feedback: list[str] | None = None
) -> list[dict[str, object]]:
    template_name = _TEMPLATE_BY_MODE[inp.mode]
    system = render(get_prompts()[template_name], goal_description=inp.goal_description)
    new_event_nos = set(inp.new_event_nos)
    lines = []
    for event in inp.events:
        marker = "【新規】" if event.event_no in new_event_nos else ""
        reason = f"（理由：{event.reason}）" if event.reason else ""
        lines.append(f"- [{event.event_no}] {marker}{event.kind}: {event.summary}{reason}")
    user = "\n".join(lines) or "（出来事なし）"
    if feedback:
        # 詳細設計書 4.2：Judge の具体的な修正指示を添えて、1回だけ作り直す
        notes = "\n".join(f"- {note}" for note in feedback)
        user += (
            f"\n\n# 前回の組み立てへの指摘（これを直して、もう一度 JSON で返してください）\n{notes}"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
