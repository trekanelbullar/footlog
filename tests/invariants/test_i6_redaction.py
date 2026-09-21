"""I6：秘密情報らしい文字列が伏せ字になること（設計書 §4 の4、§8）。"""

import pytest

from ai_hackathon_team_a.redact import PLACEHOLDER, redact

_REPRESENTATIVE_SECRETS = [
    "sk-abcdefghijklmnopqrstuvwx",
    "sk-proj-abcdefghijklmnopqrstuvwx",
    "AKIAABCDEFGHIJKLMNOP",
    "ghp_abcdefghijklmnopqrstuvwxyz01",
    "github_pat_abcdefghijklmnopqrstuvwxyz0123456789",
    "xoxb-1234567890-abcdefghij",
    "AIzaSyAbcdefghijklmnopqrstuvwxyz012345",
]


@pytest.mark.parametrize("secret", _REPRESENTATIVE_SECRETS)
def test_representative_key_shapes_are_redacted(secret: str) -> None:
    text = f"設定値: {secret} を使う"

    redacted, count = redact(text)

    assert secret not in redacted
    assert PLACEHOLDER in redacted
    assert count == 1


def test_private_key_block_is_redacted() -> None:
    block = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAK...\nmoreBase64Data==\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = f"secret:\n{block}\nend"

    redacted, count = redact(text)

    assert "BEGIN RSA PRIVATE KEY" not in redacted
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
    text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"

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
