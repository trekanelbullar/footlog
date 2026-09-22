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


def test_regeneration_passes_judge_notes_to_the_second_assembly() -> None:
    """Judge が不合格なら、指摘を添えて1回だけ組み立て直す（詳細設計書 4.2）。"""

    import json as _json

    from ai_hackathon_team_a.llm import LlmResult
    from ai_hackathon_team_a.pipeline.contracts import DIFF_HEADINGS, AssembleInput, EventSummary
    from ai_hackathon_team_a.pipeline.judge import assemble_and_judge

    assemble_messages: list[list[dict[str, object]]] = []
    judge_answers = iter(
        [
            {"status": "fail", "notes": ["決定の理由が書かれていない"]},
            {"status": "pass", "notes": []},
        ]
    )

    def fake_llm(stage, messages, *, json_mode=False, tools=None):
        if stage == "ASSEMBLE":
            assemble_messages.append(messages)
            text = _json.dumps(
                {
                    "sections": {"decisions": [{"text": "Xを採用", "event_nos": [1]}]},
                    "summary_for_mail": "要約",
                }
            )
        else:
            text = _json.dumps(next(judge_answers))
        return LlmResult(
            text=text, tool_calls=None, input_tokens=1, output_tokens=1, model="m", estimated=True
        )

    result = assemble_and_judge(
        AssembleInput(
            mode="diff",
            goal_description="G",
            events=[EventSummary(event_no=1, kind="decision", summary="Xを採用", reason=None)],
            new_event_nos=[1],
        ),
        fake_llm,
        headings=DIFF_HEADINGS,
        valid_event_nos={1},
        unsupported_event_nos=set(),
    )

    assert result.judge_status == "pass"
    assert len(assemble_messages) == 2
    assert "決定の理由が書かれていない" not in str(assemble_messages[0])
    assert "決定の理由が書かれていない" in str(assemble_messages[1])
