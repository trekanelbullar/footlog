"""図の生成（設計書 §5.3 (11)、spec E6〜E8、C12）。

ラベルに入りうる文字を、Mermaid の構文に影響しない置換後の集合に限定することで、
文法エラーが起きないことを作りで保証する（サーバー側で公式パーサは使わない）。
"""

from dataclasses import dataclass
from datetime import datetime

from ai_hackathon_team_a.pipeline.contracts import EventKind

_MAX_LABEL_CHARS = 30
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


def build_mermaid(events: list[MermaidEvent]) -> str:
    """``events`` から ``flowchart TD`` の Mermaid DSL を組み立てる。"""

    ordered = sorted(events, key=lambda e: e.occurred_at)
    present_event_nos = {e.event_no for e in ordered}

    lines = ["flowchart TD"]
    for event in ordered:
        open_bracket, close_bracket = _SHAPE_BY_KIND[event.kind]
        label = _sanitize_label(event.summary)
        lines.append(f"  E{event.event_no}{open_bracket}{label}{close_bracket}")

    for event in ordered:
        if event.supersedes_event_no is not None and event.supersedes_event_no in present_event_nos:
            lines.append(f"  E{event.event_no} -.-> E{event.supersedes_event_no}")

    return "\n".join(lines)


def _sanitize_label(summary: str) -> str:
    text = summary.replace("\n", " ").replace("\r", " ")
    for char, replacement in _FULLWIDTH_MAP.items():
        text = text.replace(char, replacement)
    if len(text) > _MAX_LABEL_CHARS:
        text = text[: _MAX_LABEL_CHARS - len(_ELLIPSIS)] + _ELLIPSIS
    return f'"{text}"'
