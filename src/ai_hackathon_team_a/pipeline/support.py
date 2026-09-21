"""裏付けの検査（設計書 §5.3 (5)）。

ここでは1段目の仮の実装として、LLM を呼ばずに入力の件数から決まった値
（すべて ``unsupported``）を返す。本実装は3段目（実行の芯）で置き換える。
"""

from ai_hackathon_team_a.pipeline.contracts import LlmFn, SupportInput, SupportOutput


def check_support(inp: SupportInput, llm: LlmFn) -> SupportOutput:
    """仮の実装：LLM を呼ばず、件数分の ``unsupported`` を返す。"""

    return SupportOutput(results=["unsupported" for _ in inp.items])
