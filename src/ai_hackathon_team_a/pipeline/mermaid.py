"""図の生成（設計書 §5.3 (11)、spec E6〜E8、C12）。

ラベルに入りうる文字を、Mermaid の構文に影響しない置換後の集合に限定することで、
文法エラーが起きないことを作りで保証する（サーバー側で公式パーサは使わない）。
"""

from dataclasses import dataclass
from datetime import datetime

from ai_hackathon_team_a.pipeline.contracts import EventKind, Topic

_MAX_LABEL_CHARS = 30
_OTHER_TOPIC = "その他"
_ELLIPSIS = "…"

# 設計書 §5.3 (11)：" ( ) [ ] { } < > # ; | & ` を全角に置換する。
_FULLWIDTH_MAP: dict[str, str] = {
    '"': "＂",
    "(": "（",
    ")": "）",
    "[": "［",
    "]": "］",
    "{": "｛",
    "}": "｝",
    "<": "＜",
    ">": "＞",
    "#": "＃",
    ";": "；",
    "|": "｜",
    "&": "＆",
    "`": "｀",
}

# 種類ごとの形（決定＝四角、却下案＝六角形、未解決＝丸、わかったこと＝平行四辺形、
# 現在地＝旗（非対称の形で代用）)。
_SHAPE_BY_KIND: dict[str, tuple[str, str]] = {
    "decision": ("[", "]"),
    "rejected_option": ("{{", "}}"),
    "open_issue": ("((", "))"),
    "finding": ("[/", "/]"),
    "status": (">", "]"),
}


@dataclass(frozen=True)
class MermaidEvent:
    """図に載せる1件の出来事。"""

    event_no: int
    kind: EventKind
    summary: str
    occurred_at: datetime
    supersedes_event_no: int | None


def build_mermaid(events: list[MermaidEvent], topics: list[Topic] | None = None) -> str:
    """``events`` から ``flowchart TD`` の Mermaid DSL を組み立てる。

    ``topics`` が無ければ：出来事を ``occurred_at`` の古い順（同じ時刻なら ``event_no`` の順）に
    並べ、隣どうしを実線の矢印でつないで1本の時系列にする。ノード同士がつながっていない
    と、`flowchart TD` でも見た目は横一列に並んでしまうため（Mermaid はノードの
    向きを辺から決める）。supersedes は今どおり点線でつなぐ。

    ``topics`` があれば：論点ごとのレーン（``subgraph T1`` …、中は ``direction LR`` で左から
    古い順）を上から順に積む。どの論点にも入らなかった出来事は最後の「その他」に入れる。
    レーンの中の並びと矢印は上と同じ。レーンをまたぐ supersedes は描かない（レーンの向きが
    崩れるため）。レーンの順は、各レーンの最も古い出来事の時刻順。
    """

    ordered = sorted(events, key=lambda e: (e.occurred_at, e.event_no))
    if not topics:
        return _single_lane(ordered)

    by_no = {e.event_no: e for e in ordered}
    lanes: list[tuple[str, list[MermaidEvent]]] = []
    assigned: set[int] = set()
    for topic in topics:
        members = [by_no[n] for n in topic.event_nos if n in by_no and n not in assigned]
        assigned.update(e.event_no for e in members)
        if members:
            lanes.append((topic.name, sorted(members, key=lambda e: (e.occurred_at, e.event_no))))
    rest = [e for e in ordered if e.event_no not in assigned]
    if rest:
        lanes.append((_OTHER_TOPIC, rest))
    if len(lanes) <= 1:
        return _single_lane(ordered)

    lanes.sort(key=lambda lane: (lane[1][0].occurred_at, lane[1][0].event_no))
    lines = ["flowchart TD"]
    for index, (name, members) in enumerate(lanes, start=1):
        lines.append(f"  subgraph T{index}[{_sanitize_label(name)}]")
        lines.append("    direction LR")
        lines.extend(f"    {_node(e)}" for e in members)
        lines.extend(
            f"    E{a.event_no} --> E{b.event_no}"
            for a, b in zip(members, members[1:], strict=False)
        )
        in_lane = {e.event_no for e in members}
        lines.extend(
            f"    E{e.event_no} -.-> E{e.supersedes_event_no}"
            for e in members
            if e.supersedes_event_no is not None and e.supersedes_event_no in in_lane
        )
        lines.append("  end")
    # レーンどうしを見えない線でつなぎ、横ではなく縦に積む。
    lines.extend(f"  T{i} ~~~ T{i + 1}" for i in range(1, len(lanes)))
    return "\n".join(lines)


def _single_lane(ordered: list[MermaidEvent]) -> str:
    present_event_nos = {e.event_no for e in ordered}
    lines = ["flowchart TD"]
    lines.extend(f"  {_node(event)}" for event in ordered)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        lines.append(f"  E{earlier.event_no} --> E{later.event_no}")
    for event in ordered:
        if event.supersedes_event_no is not None and event.supersedes_event_no in present_event_nos:
            lines.append(f"  E{event.event_no} -.-> E{event.supersedes_event_no}")
    return "\n".join(lines)


def _node(event: MermaidEvent) -> str:
    open_bracket, close_bracket = _SHAPE_BY_KIND[event.kind]
    return f"E{event.event_no}{open_bracket}{_sanitize_label(event.summary)}{close_bracket}"


def _sanitize_label(summary: str) -> str:
    text = summary.replace("\n", " ").replace("\r", " ")
    for char, replacement in _FULLWIDTH_MAP.items():
        text = text.replace(char, replacement)
    if len(text) > _MAX_LABEL_CHARS:
        text = text[: _MAX_LABEL_CHARS - len(_ELLIPSIS)] + _ELLIPSIS
    return f'"{text}"'
