"""Apply pending migrations to Supabase (postgres user), nothing else.

Supabase に、まだ流していないマイグレーションだけを流す。

- 管理者（postgres）の接続文字列を、画面に表示しない形で聞く（setup_supabase.py と同じ）
- 流す前に、適用済みと未適用のファイル名を表示して確認を求める
- app_worker のログインやパスワードには触れない
- 接続文字列とパスワードは、画面・ログ・ファイル・シェルの履歴のどこにも残さない

実行：Mac のターミナルアプリで、リポジトリ直下から ``uv run python scripts/migrate_supabase.py``
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import psycopg

from ai_hackathon_team_a.db_migrate import DEFAULT_MIGRATIONS_DIR

_SETUP = Path(__file__).resolve().parent / "setup_supabase.py"
_spec = importlib.util.spec_from_file_location("setup_supabase", _SETUP)
assert _spec and _spec.loader
setup = importlib.util.module_from_spec(_spec)
sys.modules["setup_supabase"] = setup
_spec.loader.exec_module(setup)


def pending_migrations(params: dict[str, str]) -> list[str]:
    """まだ流していないマイグレーションのファイル名を返す。"""

    all_files = sorted(p.name for p in DEFAULT_MIGRATIONS_DIR.glob("*.sql"))
    with psycopg.connect(**params) as conn:
        exists = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
        applied = (
            {row[0] for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()}
            if exists
            else set()
        )
    return [name for name in all_files if name not in applied]


def main() -> int:
    say = setup.say
    say("Supabase にマイグレーションを流します（接続文字列は画面に出しません）。")
    try:
        say("")
        say("  Supabase の管理画面の「Connect」→「Session pooler」の接続文字列を、")
        say("  [YOUR-PASSWORD] をデータベースのパスワードに置き換えてから貼り付けてください。")
        params, _target = setup.parse_admin(setup.ask_secret("  接続文字列"))
        try:
            setup.check_admin(params)
            pending = pending_migrations(params)
        except psycopg.Error as exc:
            raise setup.SetupError(setup.explain_connect_error(exc)) from None

        if not pending:
            say("  流すべきマイグレーションはありません（すべて適用済み）。")
            return 0
        say("  これから流すマイグレーション：")
        for name in pending:
            say(f"    - {name}")
        if not setup.confirm("  流しますか"):
            say("  中止しました。")
            return 1
        try:
            setup.run_migrations(params)
        except psycopg.Error as exc:
            raise setup.SetupError(
                f"マイグレーションに失敗しました（種類：{type(exc).__name__}）。"
                "途中まで適用されたファイルはありません（1ファイルずつトランザクションで流しています）。"
            ) from None
    except setup.SetupError as exc:
        say("")
        say(f"  止まりました：{exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        say("")
        say("  中止しました。")
        return 130
    finally:
        if "params" in locals():
            params.clear()

    say("")
    say("マイグレーションが終わりました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
