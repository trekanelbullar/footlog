import pytest
from pydantic import ValidationError

from ai_hackathon_team_a.config import Settings


def test_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_http_base_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            _env_file=None,
            api_key="test-key",
            base_url="http://api.example.test/v1",
        )


def test_secret_is_redacted() -> None:
    settings = Settings(_env_file=None, api_key="do-not-display")

    assert "do-not-display" not in repr(settings)
