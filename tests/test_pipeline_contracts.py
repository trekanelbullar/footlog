from datetime import UTC, datetime
from unittest.mock import Mock

from ai_hackathon_team_a.pipeline.assemble import assemble_report
from ai_hackathon_team_a.pipeline.contracts import (
    AssembleInput,
    EventSummary,
    ExtractedEvent,
    ExtractInput,
    ExtractOutput,
    SegmentIn,
    SupportInput,
    SupportItem,
    SupportOutput,
)
from ai_hackathon_team_a.pipeline.extract import extract_events
from ai_hackathon_team_a.pipeline.support import check_support

_NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _segment(label: str = "S3-1") -> SegmentIn:
    return SegmentIn(label=label, speaker="user", recorded_at=_NOW, text="hello")


def _event() -> ExtractedEvent:
    return ExtractedEvent(
        kind="decision",
        summary="配信基盤はResendを採用",
        reason="値段が手頃だから",
        occurred_at=_NOW,
        segment_ids=["S3-1"],
        origin="human_originated",
        supersedes_event_no=None,
        conflicts_with_event_no=None,
    )


def test_extract_output_json_round_trip() -> None:
    output = ExtractOutput(
        events=[_event()], suspected_injection_segment_ids=["S7-3"], dropped_segment_ids=[]
    )

    restored = ExtractOutput.model_validate_json(output.model_dump_json())

    assert restored == output


def test_support_input_json_round_trip() -> None:
    inp = SupportInput(items=[SupportItem(event=_event(), segments=[_segment()])])

    restored = SupportInput.model_validate_json(inp.model_dump_json())

    assert restored == inp


def test_assemble_output_json_round_trip() -> None:
    output = assemble_report(
        AssembleInput(
            mode="diff",
            goal_description="ゴール",
            events=[EventSummary(event_no=1, kind="decision", summary="x", reason=None)],
            new_event_nos=[1],
        ),
        llm=Mock(),
    )

    restored = type(output).model_validate_json(output.model_dump_json())

    assert restored == output


def test_extract_events_does_not_call_llm() -> None:
    llm = Mock()
    inp = ExtractInput(goal_description="ゴール", segments=[_segment()], active_events=[])

    output = extract_events(inp, llm)

    llm.assert_not_called()
    assert output.events == []


def test_check_support_does_not_call_llm_and_matches_item_count() -> None:
    llm = Mock()
    inp = SupportInput(
        items=[
            SupportItem(event=_event(), segments=[_segment("S3-1")]),
            SupportItem(event=_event(), segments=[_segment("S3-2")]),
        ]
    )

    output = check_support(inp, llm)

    llm.assert_not_called()
    assert output == SupportOutput(results=["unsupported", "unsupported"])


def test_assemble_report_does_not_call_llm() -> None:
    llm = Mock()
    inp = AssembleInput(mode="baseline", goal_description="ゴール", events=[], new_event_nos=[])

    output = assemble_report(inp, llm)

    llm.assert_not_called()
    assert output.sections == {}
