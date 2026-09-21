"""出来事の抽出（設計書 §5.3 (3)）。

ここでは1段目の仮の実装として、LLM を呼ばずに入力から決まった値（出来事なし）を
返す。本実装は3段目（実行の芯）で置き換える。
"""

from ai_hackathon_team_a.pipeline.contracts import ExtractInput, ExtractOutput, LlmFn


def extract_events(inp: ExtractInput, llm: LlmFn) -> ExtractOutput:
    """仮の実装：LLM を呼ばず、出来事なしの決まった値を返す。"""

    return ExtractOutput(events=[], suspected_injection_segment_ids=[], dropped_segment_ids=[])
