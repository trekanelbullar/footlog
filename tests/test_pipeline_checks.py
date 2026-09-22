"""``apply_mechanical_checks`` の「（根拠なし）」判定（設計書 §5.3 (10)、spec F2・C10）。

ベースラインモードの「プロジェクトの目的」（見出しID ``purpose``）は、会話の区切り
ではなくプロジェクトの目的の説明から書く文なので、根拠0件でも「（根拠なし）」を
付けず、脚注の無い文の数にも数えない。手元の本物の LLM での確認で見つかった不具合
の修正。
"""

from ai_hackathon_team_a.pipeline.checks import apply_mechanical_checks
from ai_hackathon_team_a.pipeline.contracts import BASELINE_HEADINGS, AssembleOutput, Sentence


def test_purpose_sentence_without_evidence_is_not_marked() -> None:
    output = AssembleOutput(
        sections={
            "purpose": [
                Sentence(text="このプロジェクトの目的はGです", event_nos=[], no_evidence=False)
            ],
            "current_state": [],
            "direction": [],
        },
        summary_for_mail="要約",
    )

    result = apply_mechanical_checks(
        output,
        headings=BASELINE_HEADINGS,
        valid_event_nos=set(),
        unsupported_event_nos=set(),
    )

    purpose_sentence = result.sections["purpose"][0]
    assert "（根拠なし）" not in purpose_sentence.text
    assert purpose_sentence.no_evidence is False
    assert result.no_evidence_count == 0


def test_other_headings_without_evidence_are_still_marked() -> None:
    output = AssembleOutput(
        sections={
            "purpose": [Sentence(text="目的はG", event_nos=[], no_evidence=False)],
            "current_state": [Sentence(text="根拠のない状況説明", event_nos=[], no_evidence=False)],
            "direction": [],
        },
        summary_for_mail="要約",
    )

    result = apply_mechanical_checks(
        output,
        headings=BASELINE_HEADINGS,
        valid_event_nos=set(),
        unsupported_event_nos=set(),
    )

    current_state_sentence = result.sections["current_state"][0]
    assert "（根拠なし）" in current_state_sentence.text
    assert current_state_sentence.no_evidence is True
    assert result.no_evidence_count == 1  # purpose は数えない
