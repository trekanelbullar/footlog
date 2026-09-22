"""Push values from .env and web/.env.local to Google Secret Manager.

.env と web/.env.local の値を、Google Cloud の Secret Manager に登録する。

- 値は gcloud に標準入力で渡す（コマンドの引数に載せないので、プロセスの一覧にも出ない）
- 画面に出すのは、秘密の名前と「作成した／新しい版を追加した／同じ値なので飛ばした」だけ
- 登録先のプロジェクトは、gcloud で今選ばれているもの（実行前に確認を求める）

実行：Mac のターミナルアプリで、リポジトリ直下から ``uv run python scripts/push_secrets.py``
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]

# (読み込むファイル, 変数名, Secret Manager の名前)
SECRETS: list[tuple[str, str, str]] = [
    (".env", "ORCAROUTER_API_KEY", "dt-orcarouter-api-key"),
    (".env", "DATABASE_URL", "dt-database-url"),
    (".env", "SUPABASE_URL", "dt-supabase-url"),
    (".env", "SUPABASE_SERVICE_ROLE_KEY", "dt-supabase-secret-key"),
    (".env", "SUPABASE_JWT_ALG", "dt-supabase-jwt-alg"),
    (".env", "RESEND_API_KEY", "dt-resend-api-key"),
    (".env", "MAIL_FROM", "dt-mail-from"),
    (".env", "WORKER_SHARED_SECRET", "dt-worker-shared-secret"),
    (".env", "CRON_SECRET", "dt-cron-secret"),
    (".env", "DAILY_COST_LIMIT_USD", "dt-daily-cost-limit-usd"),
    (".env", "SYSTEM_ALERT_EMAIL", "dt-system-alert-email"),
    ("web/.env.local", "NEXT_PUBLIC_SUPABASE_ANON_KEY", "dt-supabase-publishable-key"),
]

Run = Callable[..., subprocess.CompletedProcess]
Say = Callable[[str], None]


class PushError(Exception):
    """利用者に見せてよい（値を含まない）メッセージだけを持つエラー。"""


def load_values(root: Path) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}
    for file_name in {f for f, _, _ in SECRETS}:
        path = root / file_name
        if not path.exists():
            raise PushError(
                f"{file_name} がありません。先に scripts/set_env.py を実行してください。"
            )
        for key, value in dotenv_values(path).items():
            values[(file_name, key)] = value or ""
    return values


def push(values: dict[tuple[str, str], str], *, run: Run, say: Say) -> None:
    missing = [key for file_name, key, _ in SECRETS if not values.get((file_name, key))]
    if missing:
        raise PushError("値が入っていない変数があります：" + "、".join(missing))

    for file_name, key, secret_name in SECRETS:
        value = values[(file_name, key)]
        exists = (
            run(
                ["gcloud", "secrets", "describe", secret_name, "--format=value(name)"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        if exists:
            current = run(
                ["gcloud", "secrets", "versions", "access", "latest", f"--secret={secret_name}"],
                capture_output=True,
                check=False,
            )
            if current.returncode == 0 and current.stdout == value.encode("utf-8"):
                say(f"  {secret_name:32} 同じ値なので飛ばしました（{key}）")
                continue
            command = ["gcloud", "secrets", "versions", "add", secret_name, "--data-file=-"]
            action = "新しい版を追加しました"
        else:
            command = [
                "gcloud",
                "secrets",
                "create",
                secret_name,
                "--replication-policy=automatic",
                "--data-file=-",
            ]
            action = "作成しました"

        result = run(command, input=value.encode("utf-8"), capture_output=True, check=False)
        if result.returncode != 0:
            # 値は標準入力で渡しているので、gcloud のエラー文に値は含まれない。
            # 理由がわかるよう、エラー文の最初の行だけを添える。
            raise PushError(
                f"{secret_name} の登録に失敗しました（gcloud の終了コード {result.returncode}）："
                f"{_first_error_line(result.stderr)}"
            )
        say(f"  {secret_name:32} {action}（{key}）")


def _first_error_line(stderr: bytes | str | None) -> str:
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return "（エラーの説明なし）"


def current_project(run: Run) -> str:
    result = run(
        ["gcloud", "config", "get-value", "project"], capture_output=True, text=True, check=False
    )
    project = (result.stdout or "").strip()
    if result.returncode != 0 or not project or project == "(unset)":
        raise PushError("gcloud のプロジェクトが選ばれていません。")
    return project


def main() -> int:
    def say(message: str) -> None:
        print(message, flush=True)

    try:
        project = current_project(subprocess.run)
        say(f"登録先のプロジェクト：{project}")
        if input(
            "このプロジェクトの Secret Manager に登録します。進めますか [y/N]: "
        ).strip().lower() not in {
            "y",
            "yes",
        }:
            say("中止しました。")
            return 1
        push(load_values(REPO_ROOT), run=subprocess.run, say=say)
    except PushError as exc:
        say(f"止まりました：{exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        say("")
        say("中止しました。")
        return 130

    say("")
    say("登録が終わりました（値は表示していません）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
