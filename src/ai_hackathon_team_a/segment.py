"""区切り（設計書 §4 の5、spec B6）。

会話・テキスト/コード・スプレッドシートの3種類の区切り方を提供する。番号
（``S{source_no}-{seq}``）の付与は呼び出し側（``ingest.py``）の役目で、ここでは
話者・原文・locator だけを持つ ``RawSegment`` の並びを返す。
"""

import re
from dataclasses import dataclass
from typing import Literal

Speaker = Literal["user", "ai", "unknown"]

_MAX_SEGMENT_CHARS = 800
_MAX_FILE_LINES = 20

# 会話の話者の目印（行頭、半角/全角コロン）。設計書 §4 の5。
_SPEAKER_TO_ROLE: dict[str, Speaker] = {
    "You": "user",
    "User": "user",
    "Human": "user",
    "ユーザー": "user",
    "あなた": "user",
    "ChatGPT": "ai",
    "Claude": "ai",
    "Gemini": "ai",
    "Assistant": "ai",
    "AI": "ai",
}
_MARKER_RE = re.compile(
    r"^(" + "|".join(re.escape(name) for name in _SPEAKER_TO_ROLE) + r")[:：][ \t]*",
    re.MULTILINE,
)
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")


@dataclass(frozen=True)
class RawSegment:
    """番号を振る前の1区切り。"""

    text: str
    speaker: Speaker | None
    locator: str | None = None


def segment_conversation(text: str) -> list[RawSegment]:
    """会話の区切り：話者の目印があれば発言単位、無ければ空行で切る。800文字超は分割する。"""

    markers = list(_MARKER_RE.finditer(text))
    if not markers:
        segments = [RawSegment(text=body, speaker="unknown") for body in _split_paragraphs(text)]
        return _apply_char_limit(segments)

    segments = []
    preamble = text[: markers[0].start()].strip()
    if preamble:
        segments.extend(
            RawSegment(text=body, speaker="unknown") for body in _split_paragraphs(preamble)
        )

    for i, marker in enumerate(markers):
        speaker = _SPEAKER_TO_ROLE[marker.group(1)]
        start = marker.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        body = text[start:end].strip()
        if body:
            segments.append(RawSegment(text=body, speaker=speaker))

    return _apply_char_limit(segments)


def segment_text_file(text: str) -> list[RawSegment]:
    """ファイル（コード・テキスト）の区切り：空行、無ければ20行ごと。"""

    segments: list[RawSegment] = []
    for paragraph in _split_paragraphs(text):
        lines = paragraph.splitlines()
        if len(lines) <= _MAX_FILE_LINES:
            segments.append(RawSegment(text=paragraph, speaker=None))
            continue
        for chunk_start in range(0, len(lines), _MAX_FILE_LINES):
            chunk = "\n".join(lines[chunk_start : chunk_start + _MAX_FILE_LINES]).strip()
            if chunk:
                segments.append(RawSegment(text=chunk, speaker=None))
    return segments


def segment_blocks(blocks: list[tuple[int, str]]) -> list[RawSegment]:
    """Word の区切り：テキストと同じ規則（空行か20行ごと）。locator は「段落3」「段落3-7」。

    ``blocks`` は (段落番号, 1行の文字)。空の文字は空行（区切りの境目）。
    """

    segments: list[RawSegment] = []
    current: list[tuple[int, str]] = []

    def flush() -> None:
        if current:
            first, last = current[0][0], current[-1][0]
            locator = f"段落{first}" if first == last else f"段落{first}-{last}"
            segments.append(
                RawSegment(text="\n".join(t for _, t in current), speaker=None, locator=locator)
            )
            current.clear()

    for number, text in blocks:
        if not text.strip():
            flush()
            continue
        current.append((number, text))
        if len(current) >= _MAX_FILE_LINES:
            flush()
    flush()
    return segments


def segment_spreadsheet(rows: list[tuple[str, str]]) -> list[RawSegment]:
    """1単位＝1区切り（スプレッドシートの1行、スライド1枚、ノート1つ）。

    ``rows`` は (locator, 文字)。
    """

    return [
        RawSegment(text=row_text, speaker=None, locator=locator)
        for locator, row_text in rows
        if row_text.strip()
    ]


def _split_paragraphs(text: str) -> list[str]:
    """空行で段落に分ける。空行が無ければ全体を1つの段落として返す。"""

    return [p.strip() for p in _BLANK_LINE_RE.split(text.strip()) if p.strip()]


def _apply_char_limit(segments: list[RawSegment]) -> list[RawSegment]:
    """800文字を超えた区切りを、段落 → それでも超えれば800文字で分割する。"""

    result: list[RawSegment] = []
    for seg in segments:
        if len(seg.text) <= _MAX_SEGMENT_CHARS:
            result.append(seg)
            continue
        for paragraph in _split_paragraphs(seg.text) or [seg.text]:
            if len(paragraph) <= _MAX_SEGMENT_CHARS:
                result.append(RawSegment(text=paragraph, speaker=seg.speaker, locator=seg.locator))
            else:
                for chunk_start in range(0, len(paragraph), _MAX_SEGMENT_CHARS):
                    chunk = paragraph[chunk_start : chunk_start + _MAX_SEGMENT_CHARS]
                    result.append(RawSegment(text=chunk, speaker=seg.speaker, locator=seg.locator))
    return result
