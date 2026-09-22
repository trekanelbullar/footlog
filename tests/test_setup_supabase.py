import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_supabase.py"
_spec = importlib.util.spec_from_file_location("setup_supabase", _SCRIPT)
assert _spec and _spec.loader
setup = importlib.util.module_from_spec(_spec)
# dataclass が型を解決するときに sys.modules を引くため、先に登録する
sys.modules["setup_supabase"] = setup
_spec.loader.exec_module(setup)

REF = "abcdefghijklmnopqrst"
SECRET = "S3cretPassw0rdThatMustNeverAppear"


def test_parse_admin_accepts_session_pooler_url() -> None:
    dsn = f"postgresql://postgres.{REF}:{SECRET}@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres"

    params, target = setup.parse_admin(dsn)

    assert target.project_ref == REF
    assert target.host == "aws-0-ap-northeast-1.pooler.supabase.com"
    assert target.port == 5432
    assert params["sslmode"] == "require"


def test_parse_admin_accepts_direct_url() -> None:
    dsn = f"postgresql://postgres:{SECRET}@db.{REF}.supabase.co:5432/postgres"

    _, target = setup.parse_admin(dsn)

    assert target.project_ref == REF


@pytest.mark.parametrize(
    "dsn",
    [
        f"postgresql://app_worker.{REF}:{SECRET}@aws-0-x.pooler.supabase.com:5432/postgres",
        f"postgresql://postgres:{SECRET}@example.com:5432/postgres",
        f"postgresql://postgres.{REF}@aws-0-x.pooler.supabase.com:5432/postgres",
        "not a connection string ::: " + SECRET,
    ],
)
def test_parse_admin_rejects_without_echoing_secret(dsn: str) -> None:
    with pytest.raises(setup.SetupError) as excinfo:
        setup.parse_admin(dsn)

    assert SECRET not in str(excinfo.value)


def test_explain_connect_error_does_not_echo_details() -> None:
    exc = Exception(f'connection to server at "h" failed: password authentication failed {SECRET}')

    message = setup.explain_connect_error(exc)

    assert "パスワードが違います" in message
    assert SECRET not in message


def test_unknown_error_shows_only_type() -> None:
    message = setup.explain_connect_error(RuntimeError(f"weird {SECRET}"))

    assert SECRET not in message
    assert "RuntimeError" in message


def test_database_url_shape_masks_password(capsys: pytest.CaptureFixture[str]) -> None:
    target = setup.Target(host="db.x", port=5432, dbname="postgres", project_ref=REF)

    setup.show_database_url_shape(target, "aws-0-ap-northeast-1.pooler.supabase.com")

    out = capsys.readouterr().out
    assert (
        f"postgresql://app_worker.{REF}:********@aws-0-ap-northeast-1.pooler.supabase.com:5432/"
        "postgres?sslmode=require" in out
    )
