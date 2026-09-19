"""Small, security-conscious client for the OpenAI-compatible Orca Router API."""

from collections.abc import Sequence
from typing import Protocol

from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam

from ai_hackathon_team_a.config import Settings


class OrcaClientError(RuntimeError):
    """A safe-to-display Orca Router client error."""


class _CompletionsAPI(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _ChatAPI(Protocol):
    completions: _CompletionsAPI


class _OpenAICompatibleClient(Protocol):
    chat: _ChatAPI


class OrcaClient:
    """Generate text through Orca Router without logging prompts or credentials."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: _OpenAICompatibleClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or OpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=str(settings.base_url),
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )

    def generate(self, prompt: str, *, system_prompt: str | None = None) -> str:
        """Return one text response for a prompt.

        Input length is bounded to reduce accidental data submission and excessive
        API usage. Provider exceptions are converted to a generic error so a CLI or
        UI does not expose request internals.
        """

        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            raise ValueError("Prompt must not be empty")
        if len(cleaned_prompt) > self._settings.max_prompt_chars:
            raise ValueError(
                f"Prompt exceeds the {self._settings.max_prompt_chars}-character limit"
            )

        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": cleaned_prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._settings.model,
                messages=messages,
                max_tokens=self._settings.max_tokens,
            )
            content = _extract_content(response)
        except OpenAIError as exc:
            raise OrcaClientError(f"Orca Router request failed ({type(exc).__name__}).") from None

        if not content:
            raise OrcaClientError("Orca Router returned an empty response.")
        return content


def _extract_content(response: object) -> str | None:
    """Extract text while keeping the public client easy to replace in tests."""

    choices: Sequence[object] = getattr(response, "choices", ())
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else None
