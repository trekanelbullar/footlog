import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "push_secrets.py"
_spec = importlib.util.spec_from_file_location("push_secrets", _SCRIPT)
assert _spec and _spec.loader
push_secrets = importlib.util.module_from_spec(_spec)
sys.modules["push_secrets"] = push_secrets
_spec.loader.exec_module(push_secrets)


def _values() -> dict[tuple[str, str], str]:
    # 偽の値。実行時に連結して作る（リポジトリに鍵の形の文字列を置かないため）
    return {
        (file_name, key): f"fake-{key.lower()}-" + "V4l9e"
        for file_name, key, _ in push_secrets.SECRETS
    }


class FakeGcloud:
    """gcloud の代わり。登録済みの秘密を覚え、呼ばれ方を記録する。"""

    def __init__(self, existing: dict[str, bytes] | None = None) -> None:
        self.store = dict(existing or {})
        self.calls: list[tuple[list[str], bytes | None]] = []

    def __call__(self, argv, *, input=None, capture_output=False, check=False, text=False):  # noqa: A002
        self.calls.append((list(argv), input))
        name = argv[3] if len(argv) > 3 else ""
        if argv[:3] == ["gcloud", "secrets", "describe"]:
            return subprocess.CompletedProcess(argv, 0 if name in self.store else 1, b"", b"")
        if argv[:4] == ["gcloud", "secrets", "versions", "access"]:
            secret = argv[5].removeprefix("--secret=")
            if secret in self.store:
                return subprocess.CompletedProcess(argv, 0, self.store[secret], b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        if argv[:3] == ["gcloud", "secrets", "create"]:
            self.store[name] = input
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[:4] == ["gcloud", "secrets", "versions", "add"]:
            self.store[argv[4]] = input
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        raise AssertionError(f"unexpected command: {argv[:4]}")


def test_values_go_through_stdin_only_and_are_not_printed() -> None:
    values = _values()
    gcloud = FakeGcloud()
    output: list[str] = []

    push_secrets.push(values, run=gcloud, say=output.append)

    for value in values.values():
        for argv, _ in gcloud.calls:
            assert all(value not in arg for arg in argv)
        assert all(value not in line for line in output)
    for file_name, key, secret_name in push_secrets.SECRETS:
        assert gcloud.store[secret_name] == values[(file_name, key)].encode("utf-8")


def test_same_value_is_skipped_and_changed_value_gets_a_new_version() -> None:
    values = _values()
    first_name = push_secrets.SECRETS[0][2]
    second_name = push_secrets.SECRETS[1][2]
    existing = {
        first_name: values[(push_secrets.SECRETS[0][0], push_secrets.SECRETS[0][1])].encode(),
        second_name: b"old",
    }
    gcloud = FakeGcloud(existing)
    output: list[str] = []

    push_secrets.push(values, run=gcloud, say=output.append)

    text = "\n".join(output)
    assert f"{first_name}" in text and "同じ値なので飛ばしました" in text
    assert any(
        argv[:5] == ["gcloud", "secrets", "versions", "add", second_name]
        for argv, _ in gcloud.calls
    )


def test_missing_value_stops_before_any_gcloud_call() -> None:
    values = _values()
    values[(".env", "DATABASE_URL")] = ""
    gcloud = FakeGcloud()

    with pytest.raises(push_secrets.PushError, match="DATABASE_URL"):
        push_secrets.push(values, run=gcloud, say=lambda _m: None)

    assert gcloud.calls == []


def test_failure_message_includes_gcloud_reason_but_not_the_value() -> None:
    values = _values()

    def failing_run(argv, *, input=None, capture_output=False, check=False, text=False):  # noqa: A002
        if argv[:3] == ["gcloud", "secrets", "describe"]:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        return subprocess.CompletedProcess(
            argv, 1, b"", b"ERROR: (gcloud.secrets.create) PERMISSION_DENIED: denied\nmore"
        )

    with pytest.raises(push_secrets.PushError) as excinfo:
        push_secrets.push(values, run=failing_run, say=lambda _m: None)

    message = str(excinfo.value)
    assert "PERMISSION_DENIED" in message
    assert all(value not in message for value in values.values())
