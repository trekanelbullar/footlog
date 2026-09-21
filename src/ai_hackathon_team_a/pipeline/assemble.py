"""レポートの組み立て（設計書 §5.3 (9)）。

ここでは1段目の仮の実装として、LLM を呼ばずに決まった値（空のレポート）を返す。
本実装は3段目（実行の芯）で置き換える。
"""

from ai_hackathon_team_a.pipeline.contracts import AssembleInput, AssembleOutput, LlmFn


def assemble_report(inp: AssembleInput, llm: LlmFn) -> AssembleOutput:
    """仮の実装：LLM を呼ばず、空のレポートを返す。"""

    return AssembleOutput(sections={}, summary_for_mail="")
