from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_hackathon_team_a.clients.orca import OrcaClient, OrcaClientError
from ai_hackathon_team_a.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, api_key="test-key", max_prompt_chars=10)


def test_generate_returns_message_content(settings: Settings) -> None:
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))]
    )

    result = OrcaClient(settings, client=sdk_client).generate("Hi")

    assert result == "hello"
    sdk_client.chat.completions.create.assert_called_once_with(
        model="qwen/qwen3.7-flash",
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=500,
    )


def test_generate_rejects_empty_prompt(settings: Settings) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        OrcaClient(settings, client=Mock()).generate("  ")


def test_generate_rejects_oversized_prompt(settings: Settings) -> None:
    with pytest.raises(ValueError, match="character limit"):
        OrcaClient(settings, client=Mock()).generate("x" * 11)


def test_generate_rejects_empty_response(settings: Settings) -> None:
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = SimpleNamespace(choices=[])

    with pytest.raises(OrcaClientError, match="empty response"):
        OrcaClient(settings, client=sdk_client).generate("Hi")
