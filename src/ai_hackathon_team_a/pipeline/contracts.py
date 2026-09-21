"""抽出・裏付けの検査・レポートの組み立ての入出力の型（設計書 §6.4、追加4）。

ここで型を固定し、仮の実装と一緒に develop へ取り込む。相方の測定用コードは、
この時点から同じ関数を呼べる。以後、型を変えるときは相方と合意してから変える。
"""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel

EventKind = Literal["decision", "rejected_option", "open_issue", "finding", "status"]
Origin = Literal["human_originated", "ai_verified", "ai_unverified", "document"]
Speaker = Literal["user", "ai", "unknown"]
SupportResult = Literal["supported", "partial", "unsupported"]

# 固定見出しのID（設計書 §5.3 (9)、spec E2）。差分モードは5つ、ベースラインモード
# （出来事の記録が空の初回）は3つ。コードがこの順で見出し名を付ける。
DIFF_HEADINGS: tuple[str, ...] = (
    "decisions",
    "reasons",
    "rejected_options",
    "next_steps",
    "current_status",
)
BASELINE_HEADINGS: tuple[str, ...] = ("purpose", "current_state", "direction")

# 見出しIDの日本語名（spec E2 の文言のまま）。
HEADING_LABELS: dict[str, str] = {
    "decisions": "今回決定したこと",
    "reasons": "その理由",
    "rejected_options": "検討したが採用しなかった選択肢",
    "next_steps": "次にやること・未解決の論点",
    "current_status": "現在地",
    "purpose": "プロジェクトの目的",
    "current_state": "現時点の状況",
    "direction": "今後の方向性",
}


class SegmentIn(BaseModel):
    """LLM に渡す1つの区切り（例：``S3-12``）。"""

    label: str
    speaker: Speaker | None
    recorded_at: datetime
    text: str


class EventSummary(BaseModel):
    """今も有効な出来事を LLM に見せるときの要約形。"""

    event_no: int
    kind: EventKind
    summary: str
    reason: str | None


class ExtractInput(BaseModel):
    """抽出の入力（約6,000文字の塊ごと）。"""

    goal_description: str
    segments: list[SegmentIn]
    active_events: list[EventSummary]


class ExtractedEvent(BaseModel):
    """抽出・番号の検査・出どころの補正を済ませた後の1件の出来事。"""

    kind: EventKind
    summary: str
    reason: str | None
    occurred_at: datetime
    segment_ids: list[str]
    origin: Origin | None
    supersedes_event_no: int | None
    conflicts_with_event_no: int | None


class ExtractOutput(BaseModel):
    """抽出の出力。"""

    events: list[ExtractedEvent]
    suspected_injection_segment_ids: list[str]
    dropped_segment_ids: list[str]  # 実在しない等で捨てた番号（測定用）


class SupportItem(BaseModel):
    """裏付けの検査に渡す1件（出来事と、その根拠の区切りの原文）。

    設計書 spec.md の ``list[tuple[ExtractedEvent, list[SegmentIn]]]`` は JSON との
    往復がしにくいため、タプルの代わりに名前付きフィールドを持つモデルに置き換えた
    （中身は同じ）。
    """

    event: ExtractedEvent
    segments: list[SegmentIn]


class SupportInput(BaseModel):
    """裏付けの検査の入力。"""

    items: list[SupportItem]


class SupportOutput(BaseModel):
    """裏付けの検査の出力。``results`` は ``items`` と同じ順。"""

    results: list[SupportResult]


class AssembleInput(BaseModel):
    """レポートの組み立ての入力（組で絞った後の出来事）。"""

    mode: Literal["diff", "baseline"]
    goal_description: str
    events: list[EventSummary]
    new_event_nos: list[int]


class Sentence(BaseModel):
    """レポート本文の1文。"""

    text: str
    event_nos: list[int]
    no_evidence: bool  # 機械の検査で「根拠なし」になったか


class AssembleOutput(BaseModel):
    """レポートの組み立ての出力。"""

    sections: dict[str, list[Sentence]]  # 固定見出しのID → 文
    summary_for_mail: str


class LlmFn(Protocol):
    """``call_llm``（設計書 §6.1）と同じ形の呼び出し可能オブジェクト。

    段階ごとの純粋関数は、この形の関数を受け取るだけで、本物の ``call_llm``
    （コスト記録つき、3段目で実装）とテスト用の偽実装のどちらも差し替えられる。
    """

    def __call__(
        self,
        stage: str,
        messages: list[dict[str, object]],
        *,
        json_mode: bool = ...,
        tools: list[dict[str, object]] | None = ...,
    ) -> object: ...
