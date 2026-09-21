"""メール送信の唯一の入口（設計書 §5.3 (12)・§6.1、Resend の REST API）。

``storage.py`` と同じ作法：``httpx.Client`` を直接組み立て、テストでは
``transport`` に ``httpx.MockTransport`` を渡して差し替える。本物の Resend に
つなぐのはここだけ。渡すのは件名・本文の文字列だけで、ソースの原文や
管理者限定の内容が紛れ込む余地は、この関数の引数の形自体が作らない
（呼び出し側が組み立てた文字列をそのまま送るだけ）。

失敗しても呼び出し元の実行やリクエストを失敗にしない、という方針は呼び出し側
（``notify.py``・``llm.py``）が実装する。ここでは成功時に Resend のメッセージID
を返し、失敗時は :class:`MailError` を投げるだけにする。
"""

import httpx

from ai_hackathon_team_a.worker_settings import WorkerSettings

_BASE_URL = "https://api.resend.com"
_TIMEOUT_SECONDS = 30.0


class MailError(RuntimeError):
    """Resend への送信が失敗したことを表す。"""


def send_mail(
    *,
    to: str,
    subject: str,
    text_body: str,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """1通のメールを送り、Resend のメッセージIDを返す。失敗時は :class:`MailError`。"""

    with _client(settings, transport=transport) as client:
        try:
            response = client.post(
                "/emails",
                json={
                    "from": settings.mail_from,
                    "to": [to],
                    "subject": subject,
                    "text": text_body,
                },
            )
        except httpx.HTTPError as exc:
            raise MailError(f"Resend request failed: {type(exc).__name__}") from None

    if response.status_code >= 400:
        raise MailError(f"Resend request failed: {response.status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        raise MailError("Resend response was not JSON") from exc

    message_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(message_id, str):
        raise MailError("Resend response missing message id")
    return message_id


def _client(settings: WorkerSettings, *, transport: httpx.BaseTransport | None) -> httpx.Client:
    key = settings.resend_api_key.get_secret_value()
    return httpx.Client(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        transport=transport,
        timeout=_TIMEOUT_SECONDS,
    )
