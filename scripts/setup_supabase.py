"""Interactive first-time setup of the Supabase database for the worker.

Supabase への初回セットアップを対話式で行う。

1. 管理者（postgres）の接続文字列を、画面に表示しない形で聞く
2. その接続でマイグレーションを流す
3. app_worker をログイン可能にする
4. psql を開き、利用者が ``\\password app_worker`` を打つだけの状態にする
5. app_worker として、プーラーのセッションモード（5432）経由で入れるかを確かめる
6. .env に書くべき DATABASE_URL の「形」を、パスワードを伏せて表示する

接続文字列とパスワードは、画面・ログ・ファイル・シェルの履歴のどこにも残さない。
例外のメッセージにも接続情報が含まれうるため、表示するのは分類した理由だけにする。

実行：リポジトリ直下で ``uv run python scripts/setup_supabase.py``
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

import psycopg
from psycopg import conninfo, sql

from ai_hackathon_team_a.db_migrate import migrate

APP_ROLE = "app_worker"
SESSION_POOLER_PORT = 5432
TRANSACTION_POOLER_PORT = 6543
POOLER_HOST_SUFFIX = ".pooler.supabase.com"
DIRECT_HOST_RE = re.compile(r"^db\.([a-z0-9]{20})\.supabase\.co$")
POOLER_USER_RE = re.compile(r"^[a-z_][a-z0-9_]*\.([a-z0-9]{20})$")
PSQL_FALLBACKS = (
    "/opt/homebrew/opt/postgresql@16/bin/psql",
    "/usr/local/opt/postgresql@16/bin/psql",
)


class SetupError(Exception):
    """利用者に見せてよい（接続情報を含まない）メッセージだけを持つエラー。"""


@dataclass(frozen=True)
class Target:
    """接続先の、秘密ではない部分（ホスト・ポート・DB 名・プロジェクトの ref）。"""

    host: str
    port: int
    dbname: str
    project_ref: str


def say(message: str = "") -> None:
    print(message, flush=True)


def step(number: int, title: str) -> None:
    say()
    say(f"── 手順 {number}：{title}")


def ask_secret(prompt: str) -> str:
    """入力を画面に表示しない形で聞く。空なら聞き直す。"""

    while True:
        value = getpass.getpass(f"{prompt}（入力は表示されません）: ").strip()
        if value:
            return value
        say("  空です。もう一度入力してください。")


def ask_visible(prompt: str) -> str:
    while True:
        value = input(f"{prompt}: ").strip()
        if value:
            return value
        say("  空です。もう一度入力してください。")


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}


def parse_admin(dsn: str) -> tuple[dict[str, str], Target]:
    """管理者の接続文字列を分解する。例外メッセージに接続文字列を含めない。"""

    try:
        params = conninfo.conninfo_to_dict(dsn)
    except Exception:
        raise SetupError(
            "接続文字列の形が読み取れませんでした。Supabase の管理画面から"
            "コピーした文字列を、[YOUR-PASSWORD] の部分を本物のパスワードに"
            "置き換えてから貼り付けてください。"
        ) from None

    host = str(params.get("host") or "")
    user = str(params.get("user") or "")
    if not host or not user or not params.get("password"):
        raise SetupError("接続文字列に、ホスト名・ユーザー名・パスワードのどれかが入っていません。")

    project_ref = ""
    direct = DIRECT_HOST_RE.match(host)
    pooled = POOLER_USER_RE.match(user)
    if direct:
        project_ref = direct.group(1)
    elif host.endswith(POOLER_HOST_SUFFIX) and pooled:
        project_ref = pooled.group(1)
    else:
        raise SetupError(
            "Supabase の接続文字列ではないようです（ホスト名が db.<ref>.supabase.co か "
            "*.pooler.supabase.com ではありません）。"
        )

    base_user = user.split(".", 1)[0]
    if base_user != "postgres":
        raise SetupError(
            "管理者の接続文字列のユーザーが postgres ではありません。"
            "マイグレーションは postgres ユーザーで流します。"
        )

    port = int(params.get("port") or 5432)
    target = Target(
        host=host,
        port=port,
        dbname=str(params.get("dbname") or "postgres"),
        project_ref=project_ref,
    )
    params.setdefault("sslmode", "require")
    params.setdefault("connect_timeout", "15")
    return {k: str(v) for k, v in params.items()}, target


def explain_connect_error(exc: BaseException) -> str:
    """接続の失敗を、接続情報を含まない理由に分類する。"""

    text = str(exc).lower()
    if "password authentication failed" in text:
        return "パスワードが違います（ユーザー名とパスワードの組み合わせが一致しません）。"
    if "tenant or user not found" in text:
        return (
            "プーラーがユーザーを見つけられませんでした。ユーザー名が "
            "<ユーザー>.<プロジェクトの ref> の形になっているか、ロールがログイン可能か"
            "（手順3）を確かめてください。"
        )
    if "role" in text and "is not permitted to log in" in text:
        return "ロールがログイン可能になっていません（手順3が済んでいるか確かめてください）。"
    if "timeout" in text or "timed out" in text:
        return "時間内に応答がありませんでした。ホスト名とネットワークを確かめてください。"
    if "could not translate host name" in text or "nodename nor servname" in text:
        return "ホスト名が見つかりません。コピーした接続文字列のホスト名を確かめてください。"
    if "connection refused" in text:
        return "接続を断られました。ポート番号を確かめてください。"
    if "ssl" in text:
        return "暗号化（SSL）の接続に失敗しました。"
    if "network is unreachable" in text or "no route to host" in text:
        return (
            "ネットワークに届きません。直接接続（db.<ref>.supabase.co）は IPv6 専用の"
            "ことがあるので、Session pooler の接続文字列を使ってください。"
        )
    return f"接続に失敗しました（種類：{type(exc).__name__}）。"


def check_admin(params: dict[str, str]) -> None:
    with psycopg.connect(**params) as conn:
        row = conn.execute(
            "SELECT current_user, rolcreaterole FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    if row is None:
        raise SetupError("ログインしたユーザーの情報が読めませんでした。")
    user, can_create_role = row
    say(f"  接続できました（ユーザー：{user}）。")
    if not can_create_role:
        raise SetupError("このユーザーにはロールを作る権限（CREATEROLE）がありません。")


def run_migrations(params: dict[str, str]) -> None:
    applied = migrate(conninfo.make_conninfo(**params))
    if applied:
        for name in applied:
            say(f"  適用しました：{name}")
    else:
        say("  適用が必要なマイグレーションはありませんでした（適用済み）。")


def enable_login(params: dict[str, str]) -> None:
    with psycopg.connect(**params, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
        if exists is None:
            raise SetupError(
                f"{APP_ROLE} ロールがありません。手順2のマイグレーションを確かめてください。"
            )
        conn.execute(sql.SQL("ALTER ROLE {} LOGIN").format(sql.Identifier(APP_ROLE)))
    say(f"  {APP_ROLE} をログイン可能にしました。")


def find_psql() -> str:
    found = shutil.which("psql")
    if found:
        return found
    for candidate in PSQL_FALLBACKS:
        if os.path.exists(candidate):
            return candidate
    raise SetupError(
        "psql が見つかりません。ターミナルで `brew install postgresql@16` を実行してから、"
        "このスクリプトをもう一度動かしてください。"
    )


def open_psql_for_password(params: dict[str, str]) -> None:
    """psql を開く。接続情報はコマンドライン引数ではなく環境変数で渡す（ps に出さない）。"""

    psql = find_psql()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(
        {
            "PGHOST": params["host"],
            "PGPORT": params.get("port", "5432"),
            "PGUSER": params["user"],
            "PGPASSWORD": params["password"],
            "PGDATABASE": params.get("dbname", "postgres"),
            "PGSSLMODE": params.get("sslmode", "require"),
            "PGCONNECT_TIMEOUT": params.get("connect_timeout", "15"),
            # psql の履歴ファイルに何も書かない
            "PSQL_HISTORY": os.devnull,
        }
    )

    say("  これから psql が開きます。次の順に入力してください：")
    say()
    say(f"    1. \\password {APP_ROLE}  と入力して Enter")
    say("    2. 新しいパスワードを入力して Enter（画面には表示されません）")
    say("    3. 同じパスワードをもう一度入力して Enter")
    say("    4. \\q  と入力して Enter（psql を閉じます）")
    say()
    say("  パスワードは32文字以上のランダムな文字列にしてください（パスワード管理")
    say("  アプリで作るのがおすすめです）。この後の手順5で、同じパスワードを使います。")
    say()
    input("  準備ができたら Enter を押してください…")

    result = subprocess.run([psql, "--no-psqlrc"], env=env, check=False)
    if result.returncode != 0:
        raise SetupError(
            "psql が正常に終わりませんでした。もう一度この手順からやり直してください。"
        )


def pooler_host_for(target: Target) -> str:
    if target.host.endswith(POOLER_HOST_SUFFIX):
        return target.host
    say("  管理者の接続文字列が直接接続だったため、プーラーのホスト名が分かりません。")
    say("  Supabase の管理画面の「Connect」→「Session pooler」に表示されている")
    say("  ホスト名（例：aws-0-ap-northeast-1.pooler.supabase.com）だけを入力してください。")
    while True:
        host = ask_visible("  プーラーのホスト名")
        if host.endswith(POOLER_HOST_SUFFIX) and "/" not in host and "@" not in host:
            return host
        say("  ホスト名だけ（*.pooler.supabase.com）を入力してください。")


def check_app_worker(target: Target, pooler_host: str) -> None:
    password = ask_secret(f"  {APP_ROLE} のパスワード（手順4で決めたもの）")
    params = {
        "host": pooler_host,
        "port": str(SESSION_POOLER_PORT),
        "user": f"{APP_ROLE}.{target.project_ref}",
        "password": password,
        "dbname": target.dbname,
        "sslmode": "require",
        "connect_timeout": "15",
    }
    try:
        with psycopg.connect(**params, autocommit=True) as conn:
            row = conn.execute(
                "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            ).fetchone()
            locked = conn.execute("SELECT pg_try_advisory_lock(424242)").fetchone()
            conn.execute("SELECT pg_advisory_unlock(424242)")
            conn.execute("SELECT count(*) FROM projects").fetchone()
    except psycopg.Error as exc:
        raise SetupError(explain_connect_error(exc)) from None
    finally:
        password = ""
        params["password"] = ""

    if row is None or row[0] != APP_ROLE:
        raise SetupError(f"入れましたが、ユーザーが {APP_ROLE} ではありませんでした。")
    if row[1] or row[2]:
        raise SetupError(
            f"{APP_ROLE} にスーパーユーザーか BYPASSRLS の権限が付いています。"
            "設計上付けない権限なので、マイグレーションを確かめてください。"
        )
    if not locked or not locked[0]:
        raise SetupError(
            "アドバイザリロックが取れませんでした。セッションモード（5432）か確かめてください。"
        )
    say(f"  OK：{APP_ROLE} としてプーラーのセッションモード（{SESSION_POOLER_PORT}）")
    say("  経由で入れました。")
    say("  表の読み取り（RLS のポリシー）と、アドバイザリロックも確かめました。")


def show_database_url_shape(target: Target, pooler_host: str) -> None:
    say()
    say("── .env に書く DATABASE_URL の形（******** の部分を、手順4で決めたパスワードに）")
    say()
    say(
        f"  DATABASE_URL=postgresql://{APP_ROLE}.{target.project_ref}:********@"
        f"{pooler_host}:{SESSION_POOLER_PORT}/{target.dbname}?sslmode=require"
    )
    say()
    say("  パスワードに記号（@ : / ? # % など）が入っている場合は、URL 用の書き方")
    say("  （パーセントエンコード）に直す必要があります。英数字だけのパスワードなら不要です。")


def main() -> int:
    say("Supabase の初回セットアップ（decision-trace worker）")
    say("接続文字列とパスワードは、画面・ログ・ファイルのどこにも残しません。")

    try:
        step(1, "管理者（postgres）の接続文字列")
        say("  Supabase の管理画面の「Connect」→「Session pooler」の接続文字列を、")
        say("  [YOUR-PASSWORD] をデータベースのパスワードに置き換えてから貼り付けてください。")
        admin_params, target = parse_admin(ask_secret("  接続文字列"))
        if target.port == TRANSACTION_POOLER_PORT:
            say("  注意：ポート 6543（トランザクションモード）です。手順2〜4はこのまま進め、")
            say("  worker 用の接続（手順5）はセッションモード（5432）で確かめます。")
        try:
            check_admin(admin_params)
        except psycopg.Error as exc:
            raise SetupError(explain_connect_error(exc)) from None

        step(2, "マイグレーション（表の作成）")
        if not confirm("  このデータベースに表を作ります。進めますか"):
            say("  中止しました。")
            return 1
        try:
            run_migrations(admin_params)
        except psycopg.Error as exc:
            raise SetupError(
                f"マイグレーションに失敗しました（種類：{type(exc).__name__}）。"
                "途中まで適用されたファイルはありません（1ファイルずつトランザクションで流しています）。"
            ) from None

        step(3, f"{APP_ROLE} をログイン可能にする")
        try:
            enable_login(admin_params)
        except psycopg.Error as exc:
            raise SetupError(explain_connect_error(exc)) from None

        step(4, f"{APP_ROLE} のパスワードを決める（psql）")
        open_psql_for_password(admin_params)

        step(5, f"{APP_ROLE} としてプーラー経由で入れるか確かめる")
        pooler_host = pooler_host_for(target)
        check_app_worker(target, pooler_host)

        show_database_url_shape(target, pooler_host)
    except SetupError as exc:
        say()
        say(f"  止まりました：{exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        say()
        say("  中止しました。")
        return 130
    finally:
        if "admin_params" in locals():
            admin_params.clear()

    say()
    say("セットアップが終わりました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
