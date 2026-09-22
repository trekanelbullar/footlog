"""I6（リポジトリ側）：秘密情報らしい文字列がリポジトリに無いこと（設計書 §8【C-X3】）。

``redact.py`` の表を使い回す（新しいライブラリは追加しない）。``.env`` の中身は
一度も開かない。ファイル名だけを ``git ls-files`` の結果から見る。
"""

import subprocess
from pathlib import Path

from ai_hackathon_team_a.redact import REDACTION_RULES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALLOWLIST_PATH = _REPO_ROOT / "tests" / "fixtures" / "allowlist.txt"
_ALLOWED_ENV_FILES = {".env.example", "web/.env.example"}
_PLACEHOLDER_MARKERS = ("replace", "your", "changeme", "xxx", "example", "todo")
_SECRET_KEY_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def _git_ls_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _load_allowlist() -> set[str]:
    if not _ALLOWLIST_PATH.exists():
        return set()
    lines = _ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}


def _find_secret_shaped_values(text: str) -> list[str]:
    """``redact.py`` の表と同じルールで、秘密情報らしい値を（伏せずに）列挙する。"""

    found: list[str] = []
    for rule in REDACTION_RULES:
        for match in rule.pattern.finditer(text):
            if rule.value_group is None:
                value = match.group(0)
            else:
                value = match.group(rule.value_group)
                if rule.skip is not None and rule.skip(value):
                    continue
                end = match.end(rule.value_group)
                if (
                    rule.reject_if_followed_by is not None
                    and text[end : end + 1] == rule.reject_if_followed_by
                ):
                    continue
            found.append(value)
    return found


def test_env_files_are_not_tracked() -> None:
    """.env・.env.*（.env.example・web/.env.example を除く）が追跡されていないこと。

    ファイル名だけを見て判定し、内容は一切開かない。
    """

    offending = [
        path
        for path in _git_ls_files()
        if (Path(path).name == ".env" or Path(path).name.startswith(".env."))
        and path not in _ALLOWED_ENV_FILES
    ]
    assert offending == [], f".env 系のファイルが追跡されています: {offending}"


def test_repository_has_no_unallowlisted_secret_shaped_strings() -> None:
    allowlist = _load_allowlist()
    offenses: list[str] = []

    for path in _git_ls_files():
        full_path = _REPO_ROOT / path
        if not full_path.is_file():
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # バイナリ・読めないファイルは対象外

        for value in _find_secret_shaped_values(content):
            if value in allowlist:
                continue
            offenses.append(f"{path}: {value!r}")

    assert offenses == [], (
        "秘密情報らしい文字列が見つかりました（allowlist.txt に無いもの）:\n" + "\n".join(offenses)
    )


def test_env_example_files_have_no_real_looking_secrets() -> None:
    """.env.example・web/.env.example の値が空か、明らかなプレースホルダーであること。

    秘密情報らしい値がまったく無いこと自体は
    ``test_repository_has_no_unallowlisted_secret_shaped_strings`` が（allowlist.txt
    込みで）確かめるので、ここでは鍵の名前をヒントに、値がプレースホルダーらしい
    形かどうかだけを見る。
    """

    for rel_path in _ALLOWED_ENV_FILES:
        full_path = _REPO_ROOT / rel_path
        if not full_path.exists():
            continue
        content = full_path.read_text(encoding="utf-8")

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            value = value.strip()
            if not value:
                continue
            looks_secret_named = any(hint in key.upper() for hint in _SECRET_KEY_HINTS)
            if looks_secret_named and not value.isdigit():
                assert any(marker in value.lower() for marker in _PLACEHOLDER_MARKERS), (
                    f"{rel_path}:{key} の値が明らかなプレースホルダーに見えません: {value!r}"
                )
