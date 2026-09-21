"""Interactive writer for .env (worker) and web/.env.local (web).

.env（worker）と web/.env.local（web）を、手で編集せずに作る対話式のスクリプト。

- 変数を1つずつ、説明と「どこで取れる値か」を表示して聞く
- 秘密の値は画面に出ない形で聞き、秘密でない値は普通に聞く
- すでに値がある変数は「設定済み」とだけ表示し、Enter だけならそのまま残す
- WORKER_SHARED_SECRET と CRON_SECRET は自動で作る（すでにあれば作り直さない）
- DATABASE_URL は Session pooler の文字列と app_worker のパスワードから組み立てる
- 書き込んだファイルは、自分だけが読める権限（600）にする
- 値は画面・ログ・シェルの履歴のどこにも出さない

実行：Mac のターミナルアプリで、リポジトリ直下から ``uv run python scripts/set_env.py``
"""

from __future__ import annotations

import getpass
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER_ENV = REPO_ROOT / ".env"
WORKER_TEMPLATE = REPO_ROOT / ".env.example"
WEB_ENV = REPO_ROOT / "web" / ".env.local"
WEB_TEMPLATE = REPO_ROOT / "web" / ".env.example"

POOLER_HOST_SUFFIX = ".pooler.supabase.com"
SESSION_POOLER_PORT = 5432
PASSWORD_PLACEHOLDER = "[YOUR-PASSWORD]"
POOLER_USER_RE = re.compile(r"^postgres\.([a-z0-9]{20})$")
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
# テンプレートに入っている「まだ設定していない」ことを表す値
PLACEHOLDER_PREFIXES = ("replace_with", "your_", "your-", "<")

Ask = Callable[[str], str]
Say = Callable[[str], None]


class SetEnvError(Exception):
    """利用者に見せてよい（値を含まない）メッセージだけを持つエラー。"""


@dataclass(frozen=True)
class Question:
    """1つの変数の聞き方。"""

    key: str
    title: str
    where: str
    secret: bool = False
    default: str | None = None


# ── .env ファイルの読み書き ─────────────────────────────────────────────


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    # 引用符なしの値の後ろのコメント（空白 + #）は値に含めない
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _quote(value: str) -> str:
    """dotenv（python-dotenv・Next.js）の両方で同じ値に読める書き方にする。"""

    if re.fullmatch(r"[A-Za-z0-9_./:@?&=%+\-]*", value):
        return value
    if "'" not in value:
        # 一重引用符の中は、変数の展開（$）もエスケープも行われない
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def is_set(value: str | None) -> bool:
    return bool(value) and not value.lower().startswith(PLACEHOLDER_PREFIXES)


class EnvFile:
    """コメントと並び順を保ったまま、値だけを差し替える。"""

    def __init__(self, path: Path, template: Path) -> None:
        self.path = path
        source = path if path.exists() else template
        self.lines = source.read_text(encoding="utf-8").splitlines() if source.exists() else []
        self.changed = not path.exists()

    def get(self, key: str) -> str | None:
        for line in self.lines:
            match = LINE_RE.match(line)
            if match and match.group(1) == key:
                return _unquote(match.group(2))
        return None

    def set(self, key: str, value: str) -> None:
        new_line = f"{key}={_quote(value)}"
        for i, line in enumerate(self.lines):
            match = LINE_RE.match(line)
            if match and match.group(1) == key:
                if self.lines[i] != new_line:
                    self.lines[i] = new_line
                    self.changed = True
                return
        self.lines.append(new_line)
        self.changed = True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 最初から 600 で作り、書く途中でも他人に読める瞬間を作らない
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(self.lines) + "\n")
        os.chmod(self.path, 0o600)


# ── 聞き方 ────────────────────────────────────────────────────────────


def ask_value(question: Question, current: str | None, ask: Ask, ask_secret: Ask, say: Say) -> str:
    """1つの変数を聞く。Enter だけなら、今の値か既定値を返す（無ければ空）。"""

    say("")
    say(f"■ {question.key}：{question.title}")
    say(f"  どこで取れるか：{question.where}")
    if is_set(current):
        say("  設定済みです。Enter だけ押すとそのまま残します。")
        say("  変えるときは新しい値を入力してください。")
    elif question.default is not None:
        say(f"  Enter だけ押すと既定値（{question.default}）を入れます。")

    reader = ask_secret if question.secret else ask
    suffix = "（入力は表示されません）" if question.secret else ""
    answer = reader(f"  値{suffix}: ").strip()

    if answer:
        return answer
    if is_set(current):
        return current or ""
    return question.default or ""


def build_database_url(pooler: str, password: str) -> str:
    """Session pooler の文字列と app_worker のパスワードから DATABASE_URL を作る。

    エラーのメッセージには、入力された文字列もパスワードも含めない。
    """

    try:
        parts = urlsplit(pooler.strip().replace(PASSWORD_PLACEHOLDER, "placeholder"))
        host = parts.hostname or ""
        port = parts.port
        user = parts.username or ""
    except ValueError:
        raise SetEnvError("接続文字列の形が読み取れませんでした。") from None

    if parts.scheme not in {"postgresql", "postgres"}:
        raise SetEnvError("postgresql:// で始まる接続文字列を貼り付けてください。")
    if not host.endswith(POOLER_HOST_SUFFIX):
        raise SetEnvError(
            "プーラーの接続文字列ではありません"
            "（ホスト名が *.pooler.supabase.com ではありません）。"
            "「Connect」→「Session pooler」の文字列を使ってください。"
        )
    if port != SESSION_POOLER_PORT:
        raise SetEnvError(
            f"ポートが {SESSION_POOLER_PORT} ではありません。Transaction pooler（6543）を"
            "選んでいる可能性があります。「Session pooler」の文字列を貼り直してください。"
            "DATABASE_URL は書き込みませんでした。"
        )
    matched = POOLER_USER_RE.match(user)
    if not matched:
        raise SetEnvError("ユーザー名が postgres.<プロジェクトの ref> の形ではありません。")
    if not password:
        raise SetEnvError("app_worker のパスワードが空です。")

    dbname = parts.path.lstrip("/") or "postgres"
    return (
        f"postgresql://app_worker.{matched.group(1)}:{quote(password, safe='')}"
        f"@{host}:{SESSION_POOLER_PORT}/{dbname}?sslmode=require"
    )


def pooler_has_real_password(pooler: str) -> bool:
    try:
        parts = urlsplit(pooler.strip().replace(PASSWORD_PLACEHOLDER, "placeholder"))
        return bool(parts.password) and parts.password != "placeholder"
    except ValueError:
        return False


def generate_secret() -> str:
    return secrets.token_hex(32)  # 64文字の16進数


# ── 全体の流れ ───────────────────────────────────────────────────────


WORKER_QUESTIONS = [
    Question(
        "ORCAROUTER_API_KEY",
        "OrcaRouter の API キー（LLM の呼び出しに使う）",
        "OrcaRouter の管理画面の API Keys",
        secret=True,
    ),
    Question(
        "SUPABASE_SERVICE_ROLE_KEY",
        "Supabase のサービスロールキー（worker がファイル置き場とユーザー検索に使う）",
        "Supabase の管理画面の Project Settings → API Keys の service_role（または secret）",
        secret=True,
    ),
    Question(
        "SUPABASE_JWT_ALG",
        "ログインのトークンの署名方式（ES256 / RS256 / HS256）",
        "Supabase の管理画面の Project Settings → JWT Keys（新しいプロジェクトは ES256）",
        default="ES256",
    ),
]

HS256_SECRET_QUESTION = Question(
    "SUPABASE_JWT_SECRET",
    "ログインのトークンの共通の秘密鍵（HS256 のときだけ使う）",
    "Supabase の管理画面の Project Settings → JWT Keys → Legacy JWT Secret",
    secret=True,
)

AFTER_DB_QUESTIONS = [
    Question(
        "RESEND_API_KEY",
        "Resend の API キー（通知メールの送信に使う）",
        "resend.com の管理画面の API Keys",
        secret=True,
    ),
    Question(
        "MAIL_FROM",
        "通知メールの送信元アドレス",
        "Resend でドメイン認証をしていなければ onboarding@resend.dev のまま",
        default="onboarding@resend.dev",
    ),
    Question(
        "DAILY_COST_LIMIT_USD",
        "1日あたりの LLM の利用コストの上限（USD）",
        "自分で決める値",
        default="5",
    ),
    Question(
        "SYSTEM_ALERT_EMAIL",
        "コスト上限の超過などを知らせる先のメールアドレス",
        "自分のメールアドレス",
    ),
]


def run(
    *,
    worker_env: EnvFile,
    web_env: EnvFile,
    ask: Ask,
    ask_secret: Ask,
    say: Say,
    new_secret: Callable[[], str] = generate_secret,
) -> None:
    for question in WORKER_QUESTIONS:
        worker_env.set(
            question.key,
            ask_value(question, worker_env.get(question.key), ask, ask_secret, say),
        )

    if worker_env.get("SUPABASE_JWT_ALG") == "HS256":
        worker_env.set(
            HS256_SECRET_QUESTION.key,
            ask_value(
                HS256_SECRET_QUESTION,
                worker_env.get(HS256_SECRET_QUESTION.key),
                ask,
                ask_secret,
                say,
            ),
        )

    # SUPABASE_URL は1回聞いて、web の NEXT_PUBLIC_SUPABASE_URL にも書く
    current_url = worker_env.get("SUPABASE_URL")
    if not is_set(current_url):
        current_url = web_env.get("NEXT_PUBLIC_SUPABASE_URL")
    supabase_url = ask_value(
        Question(
            "SUPABASE_URL",
            "Supabase のプロジェクトの URL（web の NEXT_PUBLIC_SUPABASE_URL にも同じ値を書く）",
            "Supabase の管理画面の Project Settings → Data API の Project URL",
        ),
        current_url,
        ask,
        ask_secret,
        say,
    )
    worker_env.set("SUPABASE_URL", supabase_url)
    web_env.set("NEXT_PUBLIC_SUPABASE_URL", supabase_url)

    web_env.set(
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        ask_value(
            Question(
                "NEXT_PUBLIC_SUPABASE_ANON_KEY",
                "Supabase の匿名キー（web のログインに使う）",
                "Supabase の管理画面の Project Settings → API Keys の anon（または publishable）",
                secret=True,
            ),
            web_env.get("NEXT_PUBLIC_SUPABASE_ANON_KEY"),
            ask,
            ask_secret,
            say,
        ),
    )

    _set_database_url(worker_env, ask, ask_secret, say)

    for question in AFTER_DB_QUESTIONS:
        worker_env.set(
            question.key,
            ask_value(question, worker_env.get(question.key), ask, ask_secret, say),
        )

    # APP_BASE_URL は両方のファイルに同じ値を書く
    current_base = worker_env.get("APP_BASE_URL")
    if not is_set(current_base):
        current_base = web_env.get("APP_BASE_URL")
    app_base_url = ask_value(
        Question(
            "APP_BASE_URL",
            "web の URL（メールのリンクと、web の送り元の確認に使う）",
            "手元で動かすなら既定値のまま",
            default="http://localhost:3000",
        ),
        current_base,
        ask,
        ask_secret,
        say,
    )
    worker_env.set("APP_BASE_URL", app_base_url)
    web_env.set("APP_BASE_URL", app_base_url)

    web_env.set(
        "WORKER_BASE_URL",
        ask_value(
            Question(
                "WORKER_BASE_URL",
                "web から見た worker の URL",
                "手元で動かすなら既定値のまま",
                default="http://localhost:8000",
            ),
            web_env.get("WORKER_BASE_URL"),
            ask,
            ask_secret,
            say,
        ),
    )

    _set_generated_secrets(worker_env, web_env, say, new_secret)


def _set_database_url(worker_env: EnvFile, ask: Ask, ask_secret: Ask, say: Say) -> None:
    say("")
    say("■ DATABASE_URL：worker が app_worker として DB につなぐ接続文字列")
    say("  Supabase の管理画面の「Connect」→「Session pooler」の文字列と、")
    say("  app_worker のパスワード（setup_supabase.py で決めたもの）から組み立てます。")
    if is_set(worker_env.get("DATABASE_URL")):
        say("  設定済みです。Enter だけ押すとそのまま残します。")
        say("  作り直すときは接続文字列を貼ってください。")
    pooler = ask("  Session pooler の文字列（[YOUR-PASSWORD] は置き換えずに）: ").strip()
    if not pooler:
        if is_set(worker_env.get("DATABASE_URL")):
            say("  そのまま残しました。")
        else:
            say("  空だったので、DATABASE_URL は未設定のままです。")
        return
    if pooler_has_real_password(pooler):
        say("  注意：貼り付けた文字列にパスワードが入っていました。その部分は使いません。")
        say("  画面に表示されてしまったので、ターミナルの画面を消しておくと安心です。")
    password = ask_secret("  app_worker のパスワード（入力は表示されません）: ")
    try:
        worker_env.set("DATABASE_URL", build_database_url(pooler, password))
    except SetEnvError as exc:
        say(f"  書き込みませんでした：{exc}")
        return
    finally:
        password = ""
    say("  DATABASE_URL を組み立てました。")


def _set_generated_secrets(
    worker_env: EnvFile, web_env: EnvFile, say: Say, new_secret: Callable[[], str]
) -> None:
    say("")
    shared = worker_env.get("WORKER_SHARED_SECRET")
    if not is_set(shared):
        shared = web_env.get("WORKER_SHARED_SECRET")
    if is_set(shared):
        say("■ WORKER_SHARED_SECRET：設定済みのものを使います（.env と web/.env.local に同じ値）。")
    else:
        shared = new_secret()
        say("■ WORKER_SHARED_SECRET：自動で作りました（.env と web/.env.local に同じ値）。")
    worker_env.set("WORKER_SHARED_SECRET", shared or "")
    web_env.set("WORKER_SHARED_SECRET", shared or "")

    cron = worker_env.get("CRON_SECRET")
    if is_set(cron) and cron != shared:
        say("■ CRON_SECRET：設定済みのものを使います。")
    else:
        cron = new_secret()
        while cron == shared:
            cron = new_secret()
        worker_env.set("CRON_SECRET", cron)
        say("■ CRON_SECRET：自動で作りました（WORKER_SHARED_SECRET とは別の値）。")


SUMMARY_KEYS = {
    ".env": [
        "ORCAROUTER_API_KEY",
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_ALG",
        "SUPABASE_JWT_SECRET",
        "RESEND_API_KEY",
        "MAIL_FROM",
        "APP_BASE_URL",
        "WORKER_SHARED_SECRET",
        "CRON_SECRET",
        "DAILY_COST_LIMIT_USD",
        "SYSTEM_ALERT_EMAIL",
    ],
    "web/.env.local": [
        "NEXT_PUBLIC_SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        "WORKER_BASE_URL",
        "WORKER_SHARED_SECRET",
        "APP_BASE_URL",
    ],
}


def show_summary(worker_env: EnvFile, web_env: EnvFile, say: Say) -> None:
    say("")
    say("── 設定の状態（値は表示しません）")
    for label, env in ((".env", worker_env), ("web/.env.local", web_env)):
        say(f"  {label}")
        for key in SUMMARY_KEYS[label]:
            if key == "SUPABASE_JWT_SECRET" and env.get("SUPABASE_JWT_ALG") != "HS256":
                state = "不要（HS256 のときだけ使う）"
            else:
                state = "設定済み" if is_set(env.get(key)) else "未設定"
            say(f"    {key:<32} {state}")


def ensure_ignored(paths: list[Path]) -> None:
    """書き込む先が Git の管理外であることを確かめる（秘密をコミットしないため）。"""

    for path in paths:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)], cwd=REPO_ROOT, check=False
        )
        if result.returncode != 0:
            raise SetEnvError(f"{path.relative_to(REPO_ROOT)} が Git の管理外になっていません。")


def main() -> int:
    def say(message: str) -> None:
        print(message, flush=True)

    say(".env と web/.env.local を作ります（値は画面にもファイル以外のどこにも残しません）。")
    try:
        ensure_ignored([WORKER_ENV, WEB_ENV])
        worker_env = EnvFile(WORKER_ENV, WORKER_TEMPLATE)
        web_env = EnvFile(WEB_ENV, WEB_TEMPLATE)
        run(
            worker_env=worker_env,
            web_env=web_env,
            ask=input,
            ask_secret=getpass.getpass,
            say=say,
        )
        worker_env.save()
        web_env.save()
        show_summary(worker_env, web_env, say)
    except SetEnvError as exc:
        say(f"止まりました：{exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        say("")
        say("中止しました。ファイルは変更していません。")
        return 130

    say("")
    say("書き込みました（どちらのファイルも、自分だけが読める権限 600 にしました）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
