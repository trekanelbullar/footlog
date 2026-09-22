"""I6：秘密情報らしい文字列が伏せ字になること（設計書 §4 の4、§8）。"""

import pytest

from ai_hackathon_team_a.redact import PLACEHOLDER, redact

# 偽の鍵は、実行時に接頭辞と本体を連結して作る。リポジトリには鍵の形の文字列を
# そのまま置かない（test_i6_repo_scan.py が、Git 管理下のファイルを同じ表で走査するため）。
_BODY = "abcdefghijklmnopqrstuvwx"
_REPRESENTATIVE_SECRETS = [
    "sk" + "-" + _BODY,
    "sk" + "-proj-" + _BODY,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "ghp" + "_" + _BODY + "yz01",
    "github" + "_pat_" + _BODY + "yz0123456789",
    "xoxb" + "-1234567890-abcdefghij",
    "AIza" + "SyAbcdefghijklmnopqrstuvwxyz012345",
]


@pytest.mark.parametrize("secret", _REPRESENTATIVE_SECRETS)
def test_representative_key_shapes_are_redacted(secret: str) -> None:
    text = f"設定値: {secret} を使う"

    redacted, count = redact(text)

    assert secret not in redacted
    assert PLACEHOLDER in redacted
    assert count == 1


def test_private_key_block_is_redacted() -> None:
    marker = "RSA PRIVATE" + " KEY"
    block = f"-----BEGIN {marker}-----\nMIIBOgIBAAJBAK...\nmoreBase64Data==\n-----END {marker}-----"
    text = f"secret:\n{block}\nend"

    redacted, count = redact(text)

    assert f"BEGIN {marker}" not in redacted
    assert count == 1


@pytest.mark.parametrize(
    "prefix", ["key=", "token=", "password=", "secret=", "passwd=", "api_key=", "KEY:"]
)
def test_key_token_password_assignment_is_redacted(prefix: str) -> None:
    text = f"{prefix}abcdEFGH1234"

    redacted, count = redact(text)

    assert "abcdEFGH1234" not in redacted
    assert count == 1


def test_bearer_token_is_redacted() -> None:
    text = "Authorization: " + "Bearer " + "abcdefghijklmnopqrstuvwxyz"

    redacted, count = redact(text)

    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert count == 1


def test_numeric_only_value_is_not_redacted() -> None:
    text = "token=8000"

    redacted, count = redact(text)

    assert redacted == text
    assert count == 0


def test_short_numeric_or_word_values_are_left_alone() -> None:
    text = "port=8080 and name=ok"

    redacted, count = redact(text)

    assert redacted == text
    assert count == 0


def test_plain_text_without_secrets_is_unchanged() -> None:
    text = "これは何の秘密も含まない普通の日本語の文章です。"

    redacted, count = redact(text)

    assert redacted == text
    assert count == 0
