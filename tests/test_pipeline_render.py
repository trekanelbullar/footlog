"""``render_markdown`` の出どころの印・未確認マーカー（設計書 §5.3 (7)(9)、spec D12・E9）。"""

from ai_hackathon_team_a.pipeline.contracts import DIFF_HEADINGS, AssembleOutput, Sentence
from ai_hackathon_team_a.pipeline.render import RenderEvent, render_markdown


def test_decision_with_no_reason_gets_unresolved_marker() -> None:
    assembled = AssembleOutput(
        sections={
            "decisions": [Sentence(text="Xを採用", event_nos=[1], no_evidence=False)],
            "reasons": [],
            "rejected_options": [],
            "next_steps": [],
            "current_status": [],
        },
        summary_for_mail="要約",
    )
    events = [
        RenderEvent(
            event_no=1,
            segment_ids=["S1-1"],
            origin="human_originated",
            kind="decision",
            reason=None,
        )
    ]

    rendered = render_markdown(assembled, events, headings=DIFF_HEADINGS)

    assert "理由は未確認：担当者に確認中" in rendered.body_markdown


def test_decision_with_reason_has_no_unresolved_marker() -> None:
    assembled = AssembleOutput(
        sections={
            "decisions": [Sentence(text="Xを採用", event_nos=[1], no_evidence=False)],
            "reasons": [],
            "rejected_options": [],
            "next_steps": [],
            "current_status": [],
        },
        summary_for_mail="要約",
    )
    events = [
        RenderEvent(
            event_no=1,
            segment_ids=["S1-1"],
            origin="human_originated",
            kind="decision",
            reason="安いから",
        )
    ]

    rendered = render_markdown(assembled, events, headings=DIFF_HEADINGS)

    assert "理由は未確認" not in rendered.body_markdown
