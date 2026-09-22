"""裏付けの検査（設計書 §5.3 (5)）。

出来事をまとめて1回の呼び出しで渡す。件数が合わなければ、その回はすべて
``unsupported`` 扱いにする（安全側に倒す）。
"""

import json

from ai_hackathon_team_a.pipeline.contracts import LlmFn, SupportInput, SupportOutput, SupportResult
from ai_hackathon_team_a.prompts import get_prompts

_VALID_RESULTS: frozenset[str] = frozenset(("supported", "partial", "unsupported"))


def check_support(inp: SupportInput, llm: LlmFn) -> SupportOutput:
    """契約は変えない（設計書 §6.4）。"""

    if not inp.items:
        return SupportOutput(results=[])

    messages = _build_messages(inp)
    result = llm("SUPPORT", messages, json_mode=True)
    text = getattr(result, "text", None)

    results = _parse_results(text) if isinstance(text, str) else None
    if results is None or len(results) != len(inp.items):
        return SupportOutput(results=["unsupported" for _ in inp.items])
    return SupportOutput(results=results)


def _parse_results(text: str) -> list[SupportResult] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    raw_results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(raw_results, list) or not all(r in _VALID_RESULTS for r in raw_results):
        return None
    return raw_results


def _build_messages(inp: SupportInput) -> list[dict[str, object]]:
    system = get_prompts()["support_check"]
    blocks = []
    for i, item in enumerate(inp.items, start=1):
        evidence = "\n".join(f"[{s.label}] {s.text}" for s in item.segments)
        reason = f"\n理由：{item.event.reason}" if item.event.reason else ""
        blocks.append(f"## 出来事{i}\n要約：{item.event.summary}{reason}\n根拠の原文：\n{evidence}")
    user = "\n\n".join(blocks)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
