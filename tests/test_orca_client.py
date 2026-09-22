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


def _completion_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        model="m",
    )


def test_complete_turns_off_thinking_for_qwen_when_reasoning_is_off() -> None:
    settings = Settings(_env_file=None, api_key="test-key")
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = _completion_response()

    OrcaClient(settings, client=sdk_client).complete(
        [{"role": "user", "content": "Hi"}], model="qwen/qwen3.7-flash", max_tokens=10
    )

    kwargs = sdk_client.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"enable_thinking": False}
    assert "reasoning_effort" not in kwargs


def test_complete_sends_reasoning_effort_and_no_disable_flag_when_reasoning_is_on() -> None:
    settings = Settings(_env_file=None, api_key="test-key")
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = _completion_response()

    OrcaClient(settings, client=sdk_client).complete(
        [{"role": "user", "content": "Hi"}],
        model="qwen/qwen3.7-flash",
        max_tokens=10,
        reasoning="low",
    )

    kwargs = sdk_client.chat.completions.create.call_args.kwargs
    assert kwargs["reasoning_effort"] == "low"
    assert "extra_body" not in kwargs


def test_complete_does_not_send_qwen_flag_to_other_models() -> None:
    settings = Settings(_env_file=None, api_key="test-key")
    sdk_client = Mock()
    sdk_client.chat.completions.create.return_value = _completion_response()

    OrcaClient(settings, client=sdk_client).complete(
        [{"role": "user", "content": "Hi"}], model="openai/gpt-4.1-mini", max_tokens=10
    )

    kwargs = sdk_client.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs
    assert "reasoning_effort" not in kwargs
