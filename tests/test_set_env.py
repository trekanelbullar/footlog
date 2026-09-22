import importlib.util
import re
import stat
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set_env.py"
_spec = importlib.util.spec_from_file_location("set_env", _SCRIPT)
assert _spec and _spec.loader
set_env = importlib.util.module_from_spec(_spec)
sys.modules["set_env"] = set_env
_spec.loader.exec_module(set_env)

REF = "abcdefghijklmnopqrst"
POOLER = (
    f"postgresql://postgres.{REF}:[YOUR-PASSWORD]"
    "@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"
)
# 偽の値。実行時に連結して作る（リポジトリに鍵の形の文字列を置かないため）
ORCA_KEY = "orca" + "-fake-" + "Z9y8x7w6v5"
SERVICE_KEY = "service" + "-fake-" + "Q1w2e3r4t5"
ANON_KEY = "anon" + "-fake-" + "A1s2d3f4g5"
RESEND_KEY = "resend" + "-fake-" + "M1n2b3v4c5"
DB_PASSWORD = "p@ss:w/rd#$Q7"  # URL と dotenv の両方で特別な意味を持つ記号を含める
SUPABASE_URL = f"https://{REF}.supabase.co"


def _templates(tmp_path: Path) -> tuple[Path, Path]:
    worker_template = tmp_path / ".env.example"
    worker_template.write_text(
        "# テンプレート\nORCAROUTER_API_KEY=replace_with_your_api_key\n"
        "ORCAROUTER_MODEL=qwen/qwen3.7-flash\nDATABASE_URL=\nSUPABASE_URL=\n"
        "SUPABASE_SERVICE_ROLE_KEY=\nSUPABASE_JWT_ALG=\nSUPABASE_JWT_SECRET=\n"
        "RESEND_API_KEY=\nMAIL_FROM=\nAPP_BASE_URL=\nWORKER_SHARED_SECRET=\nCRON_SECRET=\n"
        "DAILY_COST_LIMIT_USD=\nSYSTEM_ALERT_EMAIL=\n",
        encoding="utf-8",
    )
    web_template = tmp_path / "web.env.example"
    web_template.write_text(
        "NEXT_PUBLIC_SUPABASE_URL=\nNEXT_PUBLIC_SUPABASE_ANON_KEY=\nWORKER_BASE_URL=\n"
        "WORKER_SHARED_SECRET=\nAPP_BASE_URL=\nWORKER_MOCK=\n",
        encoding="utf-8",
    )
    return worker_template, web_template


class Script:
    """入力を台本どおりに返し、出力を記録する。"""

    def __init__(self, plain: list[str], secret: list[str]) -> None:
        self.plain = list(plain)
        self.secret = list(secret)
        self.output: list[str] = []

    def ask(self, prompt: str) -> str:
        self.output.append(prompt)
        return self.plain.pop(0)

    def ask_secret(self, prompt: str) -> str:
        self.output.append(prompt)
        return self.secret.pop(0)

    def say(self, message: str) -> None:
        self.output.append(message)

    @property
    def text(self) -> str:
        return "\n".join(self.output)


def _run(tmp_path: Path, script: Script) -> tuple[Path, Path]:
    worker_template, web_template = _templates(tmp_path)
    worker_path = tmp_path / ".env"
    web_path = tmp_path / "web" / ".env.local"
    worker_env = set_env.EnvFile(worker_path, worker_template)
    web_env = set_env.EnvFile(web_path, web_template)
    set_env.run(
        worker_env=worker_env,
        web_env=web_env,
        ask=script.ask,
        ask_secret=script.ask_secret,
        say=script.say,
    )
    worker_env.save()
    web_env.save()
    set_env.show_summary(worker_env, web_env, script.say)
    return worker_path, web_path


def _first_run_script(pooler: str = POOLER) -> Script:
    # 普通に聞く値の順：JWT_ALG, SUPABASE_URL, pooler, MAIL_FROM, DAILY_COST, ALERT_EMAIL,
    # APP_BASE_URL, WORKER_BASE_URL
    plain = ["", SUPABASE_URL, pooler, "", "", "me@example.com", "", ""]
    # 秘密の順：ORCA, SERVICE_ROLE, ANON, app_worker のパスワード, RESEND
    secret = [ORCA_KEY, SERVICE_KEY, ANON_KEY, DB_PASSWORD, RESEND_KEY]
    return Script(plain, secret)


def test_first_run_writes_values_without_echoing_them(tmp_path: Path) -> None:
    script = _first_run_script()

    worker_path, web_path = _run(tmp_path, script)

    worker = dotenv_values(worker_path)
    web = dotenv_values(web_path)
    assert worker["ORCAROUTER_API_KEY"] == ORCA_KEY
    assert worker["SUPABASE_SERVICE_ROLE_KEY"] == SERVICE_KEY
    assert worker["RESEND_API_KEY"] == RESEND_KEY
    assert worker["SUPABASE_URL"] == SUPABASE_URL == web["NEXT_PUBLIC_SUPABASE_URL"]
    assert web["NEXT_PUBLIC_SUPABASE_ANON_KEY"] == ANON_KEY
    # 既定値
    assert worker["SUPABASE_JWT_ALG"] == "ES256"
    assert worker["MAIL_FROM"] == "onboarding@resend.dev"
    assert worker["DAILY_COST_LIMIT_USD"] == "5"
    assert worker["APP_BASE_URL"] == "http://localhost:3000" == web["APP_BASE_URL"]
    assert web["WORKER_BASE_URL"] == "http://localhost:8000"
    # テンプレートの既存の行はそのまま残る
    assert worker["ORCAROUTER_MODEL"] == "qwen/qwen3.7-flash"

    # 画面に出たものに、秘密の値が1つも含まれない
    for value in (ORCA_KEY, SERVICE_KEY, ANON_KEY, RESEND_KEY, DB_PASSWORD, worker["DATABASE_URL"]):
        assert value not in script.text
    assert worker["WORKER_SHARED_SECRET"] not in script.text
    assert worker["CRON_SECRET"] not in script.text


def test_database_url_is_built_for_app_worker(tmp_path: Path) -> None:
    worker_path, _ = _run(tmp_path, _first_run_script())

    url = dotenv_values(worker_path)["DATABASE_URL"]

    assert url.startswith(f"postgresql://app_worker.{REF}:")
    assert url.endswith("@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require")
    # 記号はパーセントエンコードされ、パスワードの生の文字列は URL に残らない
    assert DB_PASSWORD not in url


def test_generated_secrets_are_distinct_hex_and_shared_with_web(tmp_path: Path) -> None:
    worker_path, web_path = _run(tmp_path, _first_run_script())

    worker = dotenv_values(worker_path)
    web = dotenv_values(web_path)
    assert re.fullmatch(r"[0-9a-f]{64}", worker["WORKER_SHARED_SECRET"])
    assert re.fullmatch(r"[0-9a-f]{64}", worker["CRON_SECRET"])
    assert worker["WORKER_SHARED_SECRET"] != worker["CRON_SECRET"]
    assert web["WORKER_SHARED_SECRET"] == worker["WORKER_SHARED_SECRET"]


def test_files_are_readable_only_by_owner(tmp_path: Path) -> None:
    worker_path, web_path = _run(tmp_path, _first_run_script())

    for path in (worker_path, web_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_second_run_keeps_existing_values_with_enter(tmp_path: Path) -> None:
    worker_path, web_path = _run(tmp_path, _first_run_script())
    before_worker = dotenv_values(worker_path)
    before_web = dotenv_values(web_path)

    # すべて Enter（DATABASE_URL の接続文字列も空）
    again = Script(plain=[""] * 8, secret=[""] * 4)
    _run(tmp_path, again)

    assert dotenv_values(worker_path) == before_worker
    assert dotenv_values(web_path) == before_web
    assert "設定済み" in again.text
    secret_keys = (
        "ORCAROUTER_API_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "RESEND_API_KEY",
        "DATABASE_URL",
        "WORKER_SHARED_SECRET",
        "CRON_SECRET",
    )
    for key in secret_keys:
        assert before_worker[key] not in again.text
    assert before_web["NEXT_PUBLIC_SUPABASE_ANON_KEY"] not in again.text


def test_transaction_pooler_port_is_refused_and_not_written(tmp_path: Path) -> None:
    script = _first_run_script(pooler=POOLER.replace(":5432/", ":6543/"))

    worker_path, _ = _run(tmp_path, script)

    assert not dotenv_values(worker_path)["DATABASE_URL"]
    assert "Transaction pooler" in script.text
    assert "DATABASE_URL                     未設定" in script.text


def test_pasted_admin_password_is_not_used(tmp_path: Path) -> None:
    admin_password = "admin" + "-fake-" + "K1j2h3"
    script = _first_run_script(pooler=POOLER.replace("[YOUR-PASSWORD]", admin_password))

    worker_path, _ = _run(tmp_path, script)

    url = dotenv_values(worker_path)["DATABASE_URL"]
    assert admin_password not in url
    assert "パスワードが入っていました" in script.text


def test_placeholder_value_counts_as_unset() -> None:
    assert not set_env.is_set("replace_with_your_api_key")
    assert not set_env.is_set("")
    assert set_env.is_set("x")


@pytest.mark.parametrize(
    "value", ["plain", "with space", "has#hash", "dollar$VAR", "q'uote", 'd"q']
)
def test_quoted_values_round_trip_through_dotenv(tmp_path: Path, value: str) -> None:
    template = tmp_path / "t"
    template.write_text("", encoding="utf-8")
    env = set_env.EnvFile(tmp_path / "e", template)
    env.set("KEY", value)
    env.save()

    assert dotenv_values(tmp_path / "e")["KEY"] == value
    assert set_env.EnvFile(tmp_path / "e", template).get("KEY") == value
