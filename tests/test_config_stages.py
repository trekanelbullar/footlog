import logging

import pytest

from ai_hackathon_team_a.config import Settings


def test_stage_config_defaults_to_top_level_model_and_max_tokens() -> None:
    settings = Settings(_env_file=None, api_key="test-key")  # type: ignore[call-arg]

    extract = settings.stage_config("EXTRACT")
    assemble = settings.stage_config("ASSEMBLE")
    support = settings.stage_config("SUPPORT")

    assert extract.model == settings.model
    assert extract.reasoning == "off"
    assert extract.max_tokens == 4000
    assert assemble.max_tokens == 4000
    # EXTRACT・ASSEMBLE 以外は既存の max_tokens（既定 500）を使う。
    assert support.max_tokens == settings.max_tokens


def test_stage_config_uses_stage_specific_overrides() -> None:
    settings = Settings(
        _env_file=None,
        api_key="test-key",
        model_judge="judge-model",
        reasoning_judge="off",
        max_tokens_judge=2000,
    )  # type: ignore[call-arg]

    judge = settings.stage_config("JUDGE")

    assert judge.model == "judge-model"
    assert judge.reasoning == "off"
    assert judge.max_tokens == 2000


def test_reasoning_effort_raises_low_max_tokens_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="ai_hackathon_team_a.config"):
        settings = Settings(
            _env_file=None,
            api_key="test-key",
            reasoning_extract="high",
            max_tokens_extract=4000,
        )  # type: ignore[call-arg]

    extract = settings.stage_config("EXTRACT")

    assert extract.max_tokens == 16_000
    assert any("EXTRACT" in record.message for record in caplog.records)


def test_reasoning_off_does_not_raise_max_tokens() -> None:
    settings = Settings(
        _env_file=None,
        api_key="test-key",
        reasoning_extract="off",
        max_tokens_extract=4000,
    )  # type: ignore[call-arg]

    extract = settings.stage_config("EXTRACT")

    assert extract.max_tokens == 4000


def test_custom_reasoning_min_max_tokens_is_respected() -> None:
    settings = Settings(
        _env_file=None,
        api_key="test-key",
        reasoning_extract="low",
        max_tokens_extract=1000,
        reasoning_min_max_tokens=2000,
    )  # type: ignore[call-arg]

    extract = settings.stage_config("EXTRACT")

    assert extract.max_tokens == 2000
