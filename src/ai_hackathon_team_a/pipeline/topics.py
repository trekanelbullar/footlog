"""図の論点のまとまり（2026-09-22 追加。ユーザー合意の案 A）。

LLM には「論点名と、そこに属する出来事の番号」だけを返させる。図のノードの文字は
今までどおり出来事の要約から、図の DSL はコード（``mermaid.py``）が組む。LLM の返事が
読めなければ空のリストを返し、図は1本の時系列のままになる。
"""

import json

from ai_hackathon_team_a.pipeline.contracts import EventSummary, LlmFn, Topic
from ai_hackathon_team_a.prompts import get_prompts, render

_MAX_TOPICS = 6
_MAX_TOPIC_NAME_CHARS = 20


def group_topics(events: list[EventSummary], llm: LlmFn, *, goal_description: str) -> list[Topic]:
    """出来事を論点ごとにまとめる。出来事が2件未満なら呼ばない（分ける意味が無い）。"""

    if len(events) < 2:
        return []
    system = render(get_prompts()["topics"], goal_description=goal_description)
    user = "\n".join(f"- [{e.event_no}] {e.kind}: {e.summary}" for e in events)
    result = llm(
        "ASSEMBLE",
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        json_mode=True,
    )
    text = getattr(result, "text", None)
    try:
        parsed = json.loads(text) if isinstance(text, str) else None
    except json.JSONDecodeError:
        parsed = None
    raw = parsed.get("topics") if isinstance(parsed, dict) else None
    return parse_topics(raw, {e.event_no for e in events})


def parse_topics(raw_topics: object, valid_event_nos: set[int]) -> list[Topic]:
    """入力に無い番号は捨て、1つの出来事は最初のまとまりだけに入れ、空のまとまりは捨て、
    多くても6つまで（入らなかった出来事は図の側で「その他」に入る）。"""

    if not isinstance(raw_topics, list):
        return []
    seen: set[int] = set()
    topics: list[Topic] = []
    for raw in raw_topics:
        if len(topics) >= _MAX_TOPICS:
            break
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        raw_nos = raw.get("event_nos")
        if not isinstance(name, str) or not name.strip() or not isinstance(raw_nos, list):
            continue
        nos = []
        for n in raw_nos:
            if (
                isinstance(n, int)
                and not isinstance(n, bool)
                and n in valid_event_nos
                and n not in seen
            ):
                seen.add(n)
                nos.append(n)
        if nos:
            topics.append(Topic(name=name.strip()[:_MAX_TOPIC_NAME_CHARS], event_nos=nos))
    return topics
