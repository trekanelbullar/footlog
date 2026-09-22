"""図の論点のまとまり（pipeline/topics.py）。"""

import json

from ai_hackathon_team_a.pipeline.contracts import EventSummary
from ai_hackathon_team_a.pipeline.topics import group_topics, parse_topics


class _Result:
    def __init__(self, text: str) -> None:
        self.text = text


def _events(*nos: int) -> list[EventSummary]:
    return [EventSummary(event_no=n, kind="decision", summary=f"e{n}", reason=None) for n in nos]


def test_parse_topics_drops_invalid_duplicate_empty_and_extra() -> None:
    raw = [
        {"name": "デプロイ", "event_nos": [1, 2, 99]},
        {"name": "通知", "event_nos": [2, 3]},
        {"name": "空", "event_nos": [99]},
        {"name": 5, "event_nos": [3]},
        {"name": "真偽値", "event_nos": [True]},
    ] + [{"name": f"余り{i}", "event_nos": [4]} for i in range(6)]

    topics = parse_topics(raw, {1, 2, 3, 4})

    assert [(t.name, t.event_nos) for t in topics] == [
        ("デプロイ", [1, 2]),
        ("通知", [3]),
        ("余り0", [4]),
    ]


def test_group_topics_calls_llm_once_with_json_mode_and_all_events() -> None:
    calls: list[tuple[str, list, bool]] = []

    def llm(stage, messages, *, json_mode=False, tools=None):
        calls.append((stage, messages, json_mode))
        return _Result(json.dumps({"topics": [{"name": "A", "event_nos": [1, 2]}]}))

    topics = group_topics(_events(1, 2, 3), llm, goal_description="G")

    assert [(t.name, t.event_nos) for t in topics] == [("A", [1, 2])]
    stage, messages, json_mode = calls[0]
    assert stage == "ASSEMBLE" and json_mode is True
    assert "JSON" in messages[0]["content"]
    assert "[3]" in messages[1]["content"]


def test_group_topics_unreadable_reply_or_too_few_events_returns_empty() -> None:
    def broken(stage, messages, *, json_mode=False, tools=None):
        return _Result("not json")

    def never(stage, messages, *, json_mode=False, tools=None):
        raise AssertionError("1件だけなら呼ばない")

    assert group_topics(_events(1, 2), broken, goal_description="G") == []
    assert group_topics(_events(1), never, goal_description="G") == []
