"""抽出の番号の検査・出どころの補正・塊分けの単体テスト（設計書 §5.3 (3)(4)）。"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from ai_hackathon_team_a.pipeline.contracts import ExtractInput, SegmentIn, chunk_segments
from ai_hackathon_team_a.pipeline.extract import extract_events

_NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _FakeResult:
    text: str


def _fake_llm(text: str):
    def _call(stage, messages, *, json_mode=False, tools=None):
        return _FakeResult(text=text)

    return _call


def _segment(label: str, *, speaker: str | None = "user", text: str = "hello") -> SegmentIn:
    return SegmentIn(label=label, speaker=speaker, recorded_at=_NOW, text=text)


# ---- 番号の検査（I4） ------------------------------------------------------


def test_unknown_segment_ids_are_dropped_and_recorded() -> None:
    llm = _fake_llm(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "X",
                        "reason": None,
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S1-1", "S99-99"],
                        "origin": "human_originated",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": ["S99-99"],
            }
        )
    )
    inp = ExtractInput(goal_description="ゴール", segments=[_segment("S1-1")], active_events=[])

    output = extract_events(inp, llm)

    assert output.events[0].segment_ids == ["S1-1"]
    assert output.dropped_segment_ids == ["S99-99"]
    # 渡した番号に無いものは suspected_injection からも外す。
    assert output.suspected_injection_segment_ids == []


def test_event_with_no_valid_segment_ids_is_dropped() -> None:
    llm = _fake_llm(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "X",
                        "reason": None,
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S99-99"],
                        "origin": None,
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    )
    inp = ExtractInput(goal_description="ゴール", segments=[_segment("S1-1")], active_events=[])

    output = extract_events(inp, llm)

    assert output.events == []


def test_supersedes_and_conflicts_must_reference_input_active_events() -> None:
    from ai_hackathon_team_a.pipeline.contracts import EventSummary

    llm = _fake_llm(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "X",
                        "reason": None,
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S1-1"],
                        "origin": None,
                        "supersedes_event_no": 999,
                        "conflicts_with_event_no": 5,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    )
    inp = ExtractInput(
        goal_description="ゴール",
        segments=[_segment("S1-1")],
        active_events=[EventSummary(event_no=5, kind="decision", summary="既存", reason=None)],
    )

    output = extract_events(inp, llm)

    assert output.events[0].supersedes_event_no is None  # 999 は入力に無い
    assert output.events[0].conflicts_with_event_no == 5  # 5 は入力にある


def test_unparseable_json_retries_once_then_gives_up() -> None:
    calls = []

    def _call(stage, messages, *, json_mode=False, tools=None):
        calls.append(1)
        return _FakeResult(text="not json")

    inp = ExtractInput(goal_description="ゴール", segments=[_segment("S1-1")], active_events=[])

    output = extract_events(inp, _call)

    assert len(calls) == 2  # 1回だけ再試行（合計2回）
    assert output.events == []


# ---- 出どころの補正 ---------------------------------------------------------


def _extract_single_event_origin(*, speaker: str | None, llm_origin: str | None) -> str | None:
    llm = _fake_llm(
        json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "X",
                        "reason": None,
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S1-1"],
                        "origin": llm_origin,
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    )
    inp = ExtractInput(
        goal_description="ゴール", segments=[_segment("S1-1", speaker=speaker)], active_events=[]
    )
    return extract_events(inp, llm).events[0].origin


def test_all_ai_segments_downgrade_human_originated_to_ai_unverified() -> None:
    assert (
        _extract_single_event_origin(speaker="ai", llm_origin="human_originated") == "ai_unverified"
    )
    assert _extract_single_event_origin(speaker="ai", llm_origin="ai_verified") == "ai_unverified"


def test_all_file_segments_become_document() -> None:
    assert _extract_single_event_origin(speaker=None, llm_origin="human_originated") == "document"


def test_unknown_speaker_clears_origin() -> None:
    assert _extract_single_event_origin(speaker="unknown", llm_origin="human_originated") is None


def test_ai_verified_origin_is_kept_when_not_all_ai() -> None:
    assert _extract_single_event_origin(speaker="user", llm_origin="ai_verified") == "ai_verified"


# ---- 塊分け（chunk_segments） -----------------------------------------------


def test_chunk_segments_splits_without_cutting_a_segment() -> None:
    segments = [_segment(f"S1-{i}", text="x" * 4000) for i in range(1, 4)]

    chunks = chunk_segments(segments, limit=6000)

    assert [len(c) for c in chunks] == [1, 1, 1]
    assert sum(len(c) for c in chunks) == 3


def test_chunk_segments_packs_small_segments_together() -> None:
    segments = [_segment(f"S1-{i}", text="x" * 100) for i in range(1, 11)]

    chunks = chunk_segments(segments, limit=6000)

    assert len(chunks) == 1
    assert len(chunks[0]) == 10


def test_chunk_segments_oversized_single_segment_is_its_own_chunk() -> None:
    segments = [_segment("S1-1", text="x" * 7000), _segment("S1-2", text="y" * 100)]

    chunks = chunk_segments(segments, limit=6000)

    assert len(chunks) == 2
    assert len(chunks[0]) == 1
    assert chunks[0][0].label == "S1-1"
