"""レポート本文（Markdown）の組み立て（設計書 §5.3 (9)、§5.6）。

機械の検査を済ませた ``AssembleOutput`` から、脚注番号・出どころの印・未確認の
件数をコードで付ける。脚注の原文そのものはここに入れない（``evidence_catalog``
は番号 → 区切りの番号（label）の対応だけを持つ。原文は表示のたびに
``source_segments.text`` から引く。I2）。
"""

from dataclasses import dataclass

from ai_hackathon_team_a.pipeline.contracts import HEADING_LABELS, AssembleOutput, EventKind, Origin

_ORIGIN_LABELS: dict[str, str] = {
    "human_originated": "〔人が発案〕",
    "ai_verified": "〔AIの提案を人が確認〕",
    "ai_unverified": "〔AIの提案のまま（未確認）〕",
    "document": "〔資料に根拠〕",
}

_DECISIONS_HEADING = "decisions"
# 理由が空のまま（エージェントが質問したが埋まらなかった等）の決定・却下案に
# 「理由は未確認」を付ける見出し（設計書 §5.3 (7)(9)、spec D12）。
_UNRESOLVED_REASON_HEADINGS = frozenset({"decisions", "rejected_options"})
_UNRESOLVED_REASON_SUFFIX = "（理由は未確認：担当者に確認中）"


@dataclass(frozen=True)
class RenderEvent:
    """本文の組み立てに要る、出来事の最小限の情報。"""

    event_no: int
    segment_ids: list[str]
    origin: Origin | None
    kind: EventKind | None = None
    reason: str | None = None
    # AD-10：過去に却下した案との類似（この版で見せてよいものだけ、呼び出し側が
    # 実効の可視性を確認済み）。
    similar_rejected_event_no: int | None = None


@dataclass(frozen=True)
class RenderedReport:
    """組み立てた本文と、脚注番号 → 区切りの番号の対応。"""

    body_markdown: str
    evidence_catalog: dict[str, list[str]]
    unverified_ai_count: int
    rejected_similarity_count: int


def render_markdown(
    assembled: AssembleOutput,
    events: list[RenderEvent],
    *,
    headings: tuple[str, ...],
) -> RenderedReport:
    """機械の検査を済ませた ``assembled`` から、最終的な Markdown を組み立てる。"""

    segment_ids_by_event_no = {event.event_no: event.segment_ids for event in events}
    origin_by_event_no = {event.event_no: event.origin for event in events}
    kind_by_event_no = {event.event_no: event.kind for event in events}
    reason_by_event_no = {event.event_no: event.reason for event in events}
    rejected_by_event_no = {
        event.event_no: event.similar_rejected_event_no
        for event in events
        if event.similar_rejected_event_no is not None
    }

    unverified_count = sum(
        1
        for sentence in assembled.sections.get(_DECISIONS_HEADING, [])
        for event_no in sentence.event_nos
        if origin_by_event_no.get(event_no) == "ai_unverified"
    )
    rejected_similarity_count = len(rejected_by_event_no)

    lines: list[str] = []
    if _DECISIONS_HEADING in headings:
        lines.append(f"未確認（AIの提案のまま）：{unverified_count}件")
    lines.append(f"過去に却下した案と類似の可能性：{rejected_similarity_count}件")
    lines.append("")

    footnote_no = 0
    evidence_catalog: dict[str, list[str]] = {}

    for heading in headings:
        lines.append(f"## {HEADING_LABELS[heading]}")
        sentences = assembled.sections.get(heading, [])
        if not sentences:
            lines.append("（該当なし）")
            lines.append("")
            continue

        for sentence in sentences:
            segment_ids: list[str] = []
            for event_no in sentence.event_nos:
                segment_ids.extend(segment_ids_by_event_no.get(event_no, []))
            footnote_marker = ""
            if segment_ids:
                footnote_no += 1
                evidence_catalog[str(footnote_no)] = list(dict.fromkeys(segment_ids))
                footnote_marker = f"[^{footnote_no}]"

            origin_marker = ""
            if heading == _DECISIONS_HEADING:
                origins = {origin_by_event_no.get(n) for n in sentence.event_nos} - {None}
                if len(origins) == 1:
                    origin_marker = f" {_ORIGIN_LABELS[next(iter(origins))]}"

            unresolved_marker = ""
            if heading in _UNRESOLVED_REASON_HEADINGS and any(
                kind_by_event_no.get(n) in ("decision", "rejected_option")
                and reason_by_event_no.get(n) is None
                for n in sentence.event_nos
            ):
                unresolved_marker = f" {_UNRESOLVED_REASON_SUFFIX}"

            rejected_nos = sorted(
                {rejected_by_event_no[n] for n in sentence.event_nos if n in rejected_by_event_no}
            )
            rejected_marker = "".join(f" 〔過去に却下した案と類似：E{m}〕" for m in rejected_nos)

            lines.append(
                f"- {sentence.text}{origin_marker}{unresolved_marker}"
                f"{rejected_marker}{footnote_marker}"
            )
        lines.append("")

    body_markdown = "\n".join(lines).rstrip() + "\n"
    return RenderedReport(
        body_markdown=body_markdown,
        evidence_catalog=evidence_catalog,
        unverified_ai_count=unverified_count,
        rejected_similarity_count=rejected_similarity_count,
    )
