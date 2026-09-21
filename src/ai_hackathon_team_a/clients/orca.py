"""Small, security-conscious client for the OpenAI-compatible Orca Router API."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from openai import OpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam

from ai_hackathon_team_a.config import Settings

ReasoningEffort = Literal["off", "low", "medium", "high"]

# usage が返らない応答の概算に使う、文字数からトークン数への大まかな換算比。
_CHARS_PER_TOKEN_ESTIMATE = 4


_QWEN_MODEL_PREFIX = "qwen/"


class OrcaClientError(RuntimeError):
    """A safe-to-display Orca Router client error."""


@dataclass(frozen=True)
class Completion:
    """``OrcaClient.complete`` の戻り値（設計書 §6.2）。"""

    text: str
    tool_calls: list[dict[str, object]] | None
    input_tokens: int
    output_tokens: int
    model: str
    estimated: bool = False


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

    def complete(
        self,
        messages: list[dict[str, object]],
        *,
        model: str,
        max_tokens: int,
        reasoning: ReasoningEffort = "off",
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> Completion:
        """段階ごとの LLM 呼び出し（設計書 §6.1・§6.2）。``llm.py`` からだけ呼ばれる。

        入力長の上限検査・``OpenAIError`` を汎用エラーに変える作法は ``generate`` と
        同じ。思考モードが ``off`` 以外のときだけ ``reasoning_effort`` を送る。``off`` で
        qwen のモデルのときは ``enable_thinking=false`` を送って思考を止める。応答に
        ``usage`` が無ければ文字数からの概算にし、``estimated=True`` を返す。
        """

        total_chars = sum(len(_message_text(m)) for m in messages)
        if total_chars == 0:
            raise ValueError("messages must not be empty")
        if total_chars > self._settings.max_prompt_chars:
            raise ValueError(
                f"messages exceed the {self._settings.max_prompt_chars}-character limit"
            )

        kwargs: dict[str, object] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if reasoning != "off":
            kwargs["reasoning_effort"] = reasoning
        elif model.startswith(_QWEN_MODEL_PREFIX):
            # qwen は既定で思考が動き、何も送らないと off にならない。ゲートウェイ経由で
            # 止まるのは enable_thinking=false だけだった（2026-09-21 に実測、追加指示 AD-6）。
            # qwen 以外のモデルには送らない（受け付けずにエラーになるおそれがあるため）。
            kwargs["extra_body"] = {"enable_thinking": False}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools

        try:
            response = self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            raise OrcaClientError(f"Orca Router request failed ({type(exc).__name__}).") from None

        choices: Sequence[object] = getattr(response, "choices", ())
        if not choices:
            raise OrcaClientError("Orca Router returned an empty response.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        text = content if isinstance(content, str) else ""
        tool_calls = _extract_tool_calls(message)

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None

        estimated = prompt_tokens is None or completion_tokens is None
        if prompt_tokens is None:
            prompt_tokens = _estimate_tokens(total_chars)
        if completion_tokens is None:
            completion_tokens = _estimate_tokens(len(text))

        response_model = getattr(response, "model", None)
        return Completion(
            text=text,
            tool_calls=tool_calls,
            input_tokens=int(prompt_tokens),
            output_tokens=int(completion_tokens),
            model=response_model if isinstance(response_model, str) else model,
            estimated=estimated,
        )


def _message_text(message: dict[str, object]) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _estimate_tokens(chars: int) -> int:
    return max(1, math.ceil(chars / _CHARS_PER_TOKEN_ESTIMATE))


def _extract_tool_calls(message: object) -> list[dict[str, object]] | None:
    raw_tool_calls = getattr(message, "tool_calls", None)
    if not raw_tool_calls:
        return None

    result: list[dict[str, object]] = []
    for call in raw_tool_calls:
        function = getattr(call, "function", None)
        result.append(
            {
                "id": getattr(call, "id", None),
                "type": getattr(call, "type", "function"),
                "function": {
                    "name": getattr(function, "name", None),
                    "arguments": getattr(function, "arguments", None),
                },
            }
        )
    return result


def _extract_content(response: object) -> str | None:
    """Extract text while keeping the public client easy to replace in tests."""

    choices: Sequence[object] = getattr(response, "choices", ())
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else None
