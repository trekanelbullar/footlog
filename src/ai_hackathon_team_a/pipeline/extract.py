"""出来事の抽出（設計書 §5.3 (3)(4)）。

塊（約6,000文字）ごとに LLM を1回呼ぶ。JSON として読めなければ1回だけ再試行し、
それでも読めなければ出来事なしとして扱う（区切りは呼び出し側で持ち越される）。
番号の検査・出どころの補正はここ（コード）で行う（I4）。
"""

import json
from datetime import datetime
from typing import get_args

from ai_hackathon_team_a.pipeline.contracts import (
    EventKind,
    EventSummary,
    ExtractedEvent,
    ExtractInput,
    ExtractOutput,
    LlmFn,
    Origin,
)
from ai_hackathon_team_a.prompts import get_prompts, render

_VALID_KINDS: frozenset[str] = frozenset(get_args(EventKind))
_VALID_ORIGINS: frozenset[str] = frozenset(get_args(Origin))
_MAX_ATTEMPTS = 2  # 1回だけ再試行（合計最大2回呼ぶ）


def extract_events(inp: ExtractInput, llm: LlmFn) -> ExtractOutput:
    """1つの塊から出来事を抽出する（契約は変えない。設計書 §6.4）。"""

    if not inp.segments:
        return ExtractOutput(events=[], suspected_injection_segment_ids=[], dropped_segment_ids=[])

    messages = _build_messages(inp)
    raw = _call_and_parse(llm, messages)
    if raw is None:
        return ExtractOutput(events=[], suspected_injection_segment_ids=[], dropped_segment_ids=[])

    valid_labels = {s.label for s in inp.segments}
    valid_active_nos = {e.event_no for e in inp.active_events}
    speaker_by_label = {s.label: s.speaker for s in inp.segments}

    events: list[ExtractedEvent] = []
    dropped: list[str] = []

    for raw_event in raw.get("events") or []:
        if not isinstance(raw_event, dict):
            continue
        kind = raw_event.get("kind")
        summary = raw_event.get("summary")
        if kind not in _VALID_KINDS or not isinstance(summary, str) or not summary.strip():
            continue

        raw_segment_ids = raw_event.get("segment_ids") or []
        if not isinstance(raw_segment_ids, list):
            raw_segment_ids = []
        kept_ids = [sid for sid in raw_segment_ids if isinstance(sid, str) and sid in valid_labels]
        dropped.extend(
            sid for sid in raw_segment_ids if not (isinstance(sid, str) and sid in valid_labels)
        )
        if not kept_ids:
            # 残りが0件の出来事は捨てる（設計書 §5.3 (4)）。
            continue

        occurred_at = _parse_datetime(raw_event.get("occurred_at")) or inp.segments[0].recorded_at
        supersedes = _valid_event_no(raw_event.get("supersedes_event_no"), valid_active_nos)
        conflicts = _valid_event_no(raw_event.get("conflicts_with_event_no"), valid_active_nos)

        origin = raw_event.get("origin")
        origin = origin if origin in _VALID_ORIGINS else None
        origin = _correct_origin(origin, kept_ids, speaker_by_label)

        events.append(
            ExtractedEvent(
                kind=kind,
                summary=summary.strip(),
                reason=_clean_optional_str(raw_event.get("reason")),
                occurred_at=occurred_at,
                segment_ids=kept_ids,
                origin=origin,
                supersedes_event_no=supersedes,
                conflicts_with_event_no=conflicts,
            )
        )

    raw_injection_ids = raw.get("suspected_injection_segment_ids") or []
    if not isinstance(raw_injection_ids, list):
        raw_injection_ids = []
    suspected_injection = [
        sid for sid in raw_injection_ids if isinstance(sid, str) and sid in valid_labels
    ]

    return ExtractOutput(
        events=events,
        suspected_injection_segment_ids=suspected_injection,
        dropped_segment_ids=list(dict.fromkeys(dropped)),
    )


def _valid_event_no(value: object, valid_nos: set[int]) -> int | None:
    return value if isinstance(value, int) and value in valid_nos else None


def _build_messages(inp: ExtractInput) -> list[dict[str, object]]:
    system = render(get_prompts()["extract"], goal_description=inp.goal_description)
    source_blocks = "\n".join(
        f'<source label="{s.label}" speaker="{s.speaker or "unknown"}" '
        f'recorded_at="{s.recorded_at.isoformat()}">\n{s.text}\n</source>'
        for s in inp.segments
    )
    user = f"# 今も有効な出来事\n{_format_events(inp.active_events)}\n\n# 資料\n{source_blocks}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _format_events(events: list[EventSummary]) -> str:
    if not events:
        return "（無し）"
    lines = []
    for event in events:
        reason = f"（理由：{event.reason}）" if event.reason else ""
        lines.append(f"- [{event.event_no}] {event.kind}: {event.summary}{reason}")
    return "\n".join(lines)


def _call_and_parse(llm: LlmFn, messages: list[dict[str, object]]) -> dict | None:
    for _ in range(_MAX_ATTEMPTS):
        result = llm("EXTRACT", messages, json_mode=True)
        text = getattr(result, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _clean_optional_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _correct_origin(
    origin: str | None, segment_ids: list[str], speaker_by_label: dict[str, str | None]
) -> str | None:
    """出どころの補正（設計書 §5.3 (4)）。"""

    speakers = {speaker_by_label.get(sid) for sid in segment_ids}
    if "unknown" in speakers:
        return None
    if speakers == {"ai"} and origin in ("human_originated", "ai_verified"):
        return "ai_unverified"
    if speakers == {None}:  # 全部ファイル由来（segment.py：ファイルは speaker=None）
        return "document"
    return origin
