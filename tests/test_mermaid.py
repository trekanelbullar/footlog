"""C12：図の生成が文法エラーを起こさないことを、正規表現で確かめる（設計書 §5.3 (11)）。"""

import re
from datetime import UTC, datetime

from ai_hackathon_team_a.pipeline.mermaid import MermaidEvent, build_mermaid

_HEADER_RE = re.compile(r"^flowchart TD$")
_NODE_RE = re.compile(
    r'^  E\d+(?:\[|\{\{|\(\(|\[/|>)"[^"\[\]{}()<>#;|&`\n\r]*"(?:\]|\}\}|\)\)|/\]|\])$'
)
# 隣どうしをつなぐ実線の矢印（時系列）。
_TIMELINE_EDGE_RE = re.compile(r"^  E\d+ --> E\d+$")
# supersedes をつなぐ点線の矢印。
_SUPERSEDES_EDGE_RE = re.compile(r"^  E\d+ -\.-> E\d+$")

_NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _event(
    event_no: int,
    kind: str,
    summary: str,
    *,
    occurred_at: datetime = _NOW,
    supersedes_event_no: int | None = None,
) -> MermaidEvent:
    return MermaidEvent(
        event_no=event_no,
        kind=kind,
        summary=summary,
        occurred_at=occurred_at,
        supersedes_event_no=supersedes_event_no,
    )


def _assert_every_line_matches(dsl: str) -> None:
    lines = dsl.splitlines()
    assert lines[0] == "flowchart TD"
    for line in lines[1:]:
        assert (
            _NODE_RE.match(line) or _TIMELINE_EDGE_RE.match(line) or _SUPERSEDES_EDGE_RE.match(line)
        ), f"line does not match the expected shape: {line!r}"


def test_all_kinds_produce_matching_shapes() -> None:
    events = [
        _event(1, "decision", "配信基盤はResendを採用"),
        _event(2, "rejected_option", "SESは却下"),
        _event(3, "open_issue", "料金プランは未確定"),
        _event(4, "finding", "無料枠で十分と判明"),
        _event(5, "status", "実装中"),
    ]

    dsl = build_mermaid(events)

    _assert_every_line_matches(dsl)
    lines = dsl.splitlines()
    # header + 5 nodes + 4 本の実線（隣どうしを時系列でつなぐ）、supersedes は無し。
    assert len(lines) == 10
    assert [line for line in lines if _TIMELINE_EDGE_RE.match(line)] == [
        "  E1 --> E2",
        "  E2 --> E3",
        "  E3 --> E4",
        "  E4 --> E5",
    ]
    assert not any(_SUPERSEDES_EDGE_RE.match(line) for line in lines)


def test_dangerous_characters_are_escaped_and_truncated() -> None:
    tricky_summary = '括弧「テスト」＆記号"(){}[]<>#;|&`\n改行<script>alert(1)</script>のあと'
    events = [_event(1, "decision", tricky_summary)]

    dsl = build_mermaid(events)

    _assert_every_line_matches(dsl)
    assert "<script>" not in dsl
    assert "\n改行" not in dsl.split("\n", 1)[1]  # ノードの行の中に生の改行が無い


def test_supersedes_draws_dotted_edge_in_occurred_at_order() -> None:
    events = [
        _event(
            2,
            "decision",
            "置き換え後",
            occurred_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
            supersedes_event_no=1,
        ),
        _event(1, "decision", "元の決定", occurred_at=datetime(2026, 9, 21, 10, tzinfo=UTC)),
    ]

    dsl = build_mermaid(events)

    _assert_every_line_matches(dsl)
    lines = dsl.splitlines()
    assert lines[1].startswith("  E1[")  # occurred_at 順（E1 が先）
    assert lines[2].startswith("  E2[")
    assert lines[3] == "  E1 --> E2"  # 隣どうしの実線（時系列）
    assert lines[4] == "  E2 -.-> E1"  # supersedes は点線


def test_supersedes_target_not_present_is_omitted() -> None:
    events = [_event(2, "decision", "単独の出来事", supersedes_event_no=999)]

    dsl = build_mermaid(events)

    _assert_every_line_matches(dsl)
    assert "-.->" not in dsl


def test_same_occurred_at_breaks_tie_by_event_no() -> None:
    events = [
        _event(3, "decision", "3番目のはずが先に渡された"),
        _event(1, "decision", "1番目"),
        _event(2, "decision", "2番目"),
    ]

    dsl = build_mermaid(events)

    _assert_every_line_matches(dsl)
    lines = dsl.splitlines()
    assert lines[1].startswith("  E1[")
    assert lines[2].startswith("  E2[")
    assert lines[3].startswith("  E3[")
    assert [line for line in lines if _TIMELINE_EDGE_RE.match(line)] == [
        "  E1 --> E2",
        "  E2 --> E3",
    ]
