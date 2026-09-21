import json
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _FakeResult:
    """``llm.LlmResult`` と同じ形（``.text`` だけ）の偽の呼び出し結果。"""

    text: str


def _fake_llm(text_by_stage: dict[str, str]) -> Mock:
    llm = Mock()
    llm.side_effect = lambda stage, messages, **kwargs: _FakeResult(text_by_stage[stage])
    return llm


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


def test_extract_events_calls_llm_and_parses_json() -> None:
    llm = _fake_llm(
        {
            "EXTRACT": json.dumps(
                {
                    "events": [
                        {
                            "kind": "decision",
                            "summary": "Xを採用",
                            "reason": "安いから",
                            "occurred_at": "2026-09-21T10:00:00+09:00",
                            "segment_ids": ["S3-1"],
                            "origin": "human_originated",
                            "supersedes_event_no": None,
                            "conflicts_with_event_no": None,
                        }
                    ],
                    "suspected_injection_segment_ids": [],
                }
            )
        }
    )
    inp = ExtractInput(goal_description="ゴール", segments=[_segment()], active_events=[])

    output = extract_events(inp, llm)

    llm.assert_called_once()
    assert [e.summary for e in output.events] == ["Xを採用"]


def test_check_support_calls_llm_once_for_all_items() -> None:
    llm = _fake_llm({"SUPPORT": json.dumps({"results": ["supported", "partial"]})})
    inp = SupportInput(
        items=[
            SupportItem(event=_event(), segments=[_segment("S3-1")]),
            SupportItem(event=_event(), segments=[_segment("S3-2")]),
        ]
    )

    output = check_support(inp, llm)

    llm.assert_called_once()
    assert output == SupportOutput(results=["supported", "partial"])


def test_check_support_falls_back_to_unsupported_on_count_mismatch() -> None:
    llm = _fake_llm({"SUPPORT": json.dumps({"results": ["supported"]})})
    inp = SupportInput(
        items=[
            SupportItem(event=_event(), segments=[_segment("S3-1")]),
            SupportItem(event=_event(), segments=[_segment("S3-2")]),
        ]
    )

    output = check_support(inp, llm)

    assert output == SupportOutput(results=["unsupported", "unsupported"])


def test_assemble_report_calls_llm_and_builds_sections() -> None:
    llm = _fake_llm(
        {
            "ASSEMBLE": json.dumps(
                {
                    "sections": {
                        "purpose": [{"text": "目的はX", "event_nos": [1]}],
                        "current_state": [],
                        "direction": [],
                    },
                    "summary_for_mail": "要約",
                }
            )
        }
    )
    inp = AssembleInput(
        mode="baseline",
        goal_description="ゴール",
        events=[EventSummary(event_no=1, kind="decision", summary="x", reason=None)],
        new_event_nos=[1],
    )

    output = assemble_report(inp, llm)

    llm.assert_called_once()
    assert output.sections["purpose"][0].text == "目的はX"
    assert output.summary_for_mail == "要約"
