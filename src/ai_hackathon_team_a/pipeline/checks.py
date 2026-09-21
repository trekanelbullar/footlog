"""レポート組み立て後の機械の検査（設計書 §5.3 (10)、spec F1〜F5、不変条件 I4）。

LLM が返した文の ``event_nos`` を、その版の入力に渡した出来事の番号だけに絞り、
根拠が0件・または ``support = unsupported`` の出来事だけを根拠にしている文には
「（根拠なし）」を付ける。文字数（1文300文字、全体6,000文字）も確認する。

ただし、ベースラインモードの「プロジェクトの目的」（見出しID ``purpose``）だけは
例外：会話の区切りではなくプロジェクトの目的の説明から書く文なので、根拠0件でも
「（根拠なし）」を付けず、脚注の無い文の数（``no_evidence_count``）にも数えない。
"""

from dataclasses import dataclass

from ai_hackathon_team_a.pipeline.contracts import AssembleOutput, Sentence

_MAX_SENTENCE_CHARS = 300
_MAX_TOTAL_CHARS = 6000
_NO_EVIDENCE_SUFFIX = "（根拠なし）"
# ベースラインモードの「プロジェクトの目的」は、会話の区切りではなくプロジェクトの
# 目的の説明から書く文なので、根拠0件でも「（根拠なし）」を付けず、脚注の無い文の
# 数にも数えない（手元の確認で見つかった不具合の修正）。
_NO_EVIDENCE_EXEMPT_HEADINGS = frozenset({"purpose"})


@dataclass(frozen=True)
class MechanicalCheckResult:
    """機械の検査を済ませたレポート本文。"""

    sections: dict[str, list[Sentence]]
    no_evidence_count: int
    cited_event_nos: frozenset[int]


def apply_mechanical_checks(
    output: AssembleOutput,
    *,
    headings: tuple[str, ...],
    valid_event_nos: set[int],
    unsupported_event_nos: set[int],
) -> MechanicalCheckResult:
    """設計書 §5.3 (10) の機械の検査を行う。"""

    checked_sections: dict[str, list[Sentence]] = {}
    no_evidence_count = 0
    cited_event_nos: set[int] = set()
    total_chars = 0

    for heading in headings:
        checked_sentences: list[Sentence] = []
        exempt = heading in _NO_EVIDENCE_EXEMPT_HEADINGS
        for sentence in output.sections.get(heading, []):
            kept_nos = [n for n in sentence.event_nos if n in valid_event_nos]
            no_evidence = not exempt and (
                not kept_nos or all(n in unsupported_event_nos for n in kept_nos)
            )

            text = sentence.text
            if len(text) > _MAX_SENTENCE_CHARS:
                text = text[: _MAX_SENTENCE_CHARS - 1] + "…"
            if no_evidence and _NO_EVIDENCE_SUFFIX not in text:
                text = f"{text}{_NO_EVIDENCE_SUFFIX}"

            if total_chars + len(text) > _MAX_TOTAL_CHARS:
                continue  # 全体の文字数上限を超える分は落とす（設計書 §5.3 (10)）。
            total_chars += len(text)

            if no_evidence:
                no_evidence_count += 1
            else:
                cited_event_nos.update(kept_nos)
            checked_sentences.append(
                Sentence(text=text, event_nos=kept_nos, no_evidence=no_evidence)
            )
        checked_sections[heading] = checked_sentences

    return MechanicalCheckResult(
        sections=checked_sections,
        no_evidence_count=no_evidence_count,
        cited_event_nos=frozenset(cited_event_nos),
    )
