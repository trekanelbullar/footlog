"""秘密情報らしい文字列の伏せ字（設計書 §4 の4、不変条件 I6）。

正規表現の表 ``REDACTION_RULES`` はモジュールの定数として公開し、取り込み時の
:func:`redact` と、リポジトリ全体を走査する ``tests/invariants/test_i6_repo_scan.py``
の両方から使い回す。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

PLACEHOLDER = "[伏せ字]"

# SCREAMING_SNAKE_CASE の定数名（``WORKER_SECRET`` の類）と、数字だけの値
# （``token=8000`` の類）は、実際のキー・トークンの形ではないため伏せない。
_LOOKS_LIKE_CONSTANT_NAME_OR_NUMBER = re.compile(r"[A-Z0-9_]+")


def _is_constant_name_or_number(value: str) -> bool:
    return bool(_LOOKS_LIKE_CONSTANT_NAME_OR_NUMBER.fullmatch(value))


@dataclass(frozen=True)
class RedactionRule:
    """1つの伏せ字ルール。

    ``value_group`` が ``None`` なら一致全体を置換する。指定があれば、その
    グループだけを置換する（前後の文字列は残す）。``skip`` があり、グループの
    中身に対して真を返す場合は置換しない（``token=8000`` の類の数字だけの値、
    ``WORKER_SECRET`` の類の定数名）。
    """

    name: str
    pattern: re.Pattern[str]
    value_group: int | None = None
    skip: Callable[[str], bool] | None = None
    # 値の直後がこの文字なら置換しない（例:"(" は関数・メソッド呼び出しの一部で
    # あり、コード中の変数代入の誤検知を減らすため）。正規表現の否定先読みは
    # 貪欲な文字クラスの再試行（バックトラック）で無力化されるため、ここでは
    # マッチ後の元の文字列を見て判定する。
    reject_if_followed_by: str | None = None


# 設計書 §4 の4（spec B8）の表。この並びで適用する：
# 1) 複数行の PRIVATE KEY ブロック
# 2) key=/token=/password= 等の代入（数字だけの値・定数名は除く。関数呼び出しの
#    直前で終わる場合は除く＝コード中の変数代入の誤検知を減らす）
# 3) Bearer トークン
# 4) 各サービス固有のキーの形（4以降は 2・3 で先に消費された残りにだけ効く）
REDACTION_RULES: tuple[RedactionRule, ...] = (
    RedactionRule(
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
    ),
    RedactionRule(
        "key_value_assignment",
        re.compile(
            r"(?i)(?:key|token|secret|password|passwd|api_key)\s*[=:]\s*([A-Za-z0-9+/_.\-]{12,})"
        ),
        value_group=1,
        skip=_is_constant_name_or_number,
        reject_if_followed_by="(",
    ),
    RedactionRule(
        "bearer_token",
        re.compile(r"Bearer\s+([A-Za-z0-9+/_.\-]{20,})"),
        value_group=1,
    ),
    RedactionRule("openai_sk_proj", re.compile(r"sk-proj-[A-Za-z0-9_\-]{10,}")),
    RedactionRule("openai_sk", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    RedactionRule("aws_akia", re.compile(r"AKIA[0-9A-Z]{16}")),
    RedactionRule("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    RedactionRule("github_ghp", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    RedactionRule("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    RedactionRule("google_aiza", re.compile(r"AIza[A-Za-z0-9_\-]{20,}")),
)


def redact(text: str) -> tuple[str, int]:
    """秘密情報らしい文字列を ``[伏せ字]`` に置換し、(置換後の文, 置換件数) を返す。"""

    result = text
    total = 0
    for rule in REDACTION_RULES:
        result, count = _apply_rule(rule, result)
        total += count
    return result, total


def _apply_rule(rule: RedactionRule, text: str) -> tuple[str, int]:
    matches = list(rule.pattern.finditer(text))
    if not matches:
        return text, 0

    pieces: list[str] = []
    last_end = 0
    count = 0
    for match in matches:
        if rule.value_group is None:
            start, end = match.span()
        else:
            start, end = match.span(rule.value_group)
            value = match.group(rule.value_group)
            if rule.skip is not None and rule.skip(value):
                continue
            if (
                rule.reject_if_followed_by is not None
                and text[end : end + 1] == rule.reject_if_followed_by
            ):
                continue
        pieces.append(text[last_end:start])
        pieces.append(PLACEHOLDER)
        last_end = end
        count += 1

    if count == 0:
        return text, 0
    pieces.append(text[last_end:])
    return "".join(pieces), count
