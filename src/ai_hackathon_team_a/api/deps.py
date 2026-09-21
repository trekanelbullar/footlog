"""ルーターが使う共通の依存関数（設計書 §1.2 の3種類の検証）。

ユーザー用ルートは ``require_worker_secret`` ＋ ``get_current_user`` の組、
cron 用ルートは ``require_cron_secret`` だけを使い、互いのヘッダーを受け付けない
（それぞれ別ルートに別々の依存関数を付けるだけで、取り違えを防ぐ）。
"""

import hmac

from fastapi import Depends, Header

from ai_hackathon_team_a.api.errors import ApiError
from ai_hackathon_team_a.auth import AuthenticatedUser, AuthError, verify_access_token
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings


def require_worker_secret(
    x_worker_secret: str | None = Header(default=None, alias="X-Worker-Secret"),
    settings: WorkerSettings = Depends(get_worker_settings),
) -> None:
    """(a) web からの呼び出しであることを共有シークレットで確認する。

    ヘッダーが無い・空のときは、比較の前に401を返す。
    """

    if not x_worker_secret:
        raise ApiError(401, "unauthorized", "X-Worker-Secret が指定されていません。")
    expected = settings.worker_shared_secret.get_secret_value()
    if not hmac.compare_digest(x_worker_secret, expected):
        raise ApiError(401, "unauthorized", "X-Worker-Secret が一致しません。")


def get_current_user(
    authorization: str | None = Header(default=None),
    settings: WorkerSettings = Depends(get_worker_settings),
) -> AuthenticatedUser:
    """(b) Authorization ヘッダーの JWT から本人のユーザーIDを取り出す。"""

    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Authorization ヘッダーが必要です。")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return verify_access_token(token, settings)
    except AuthError as exc:
        raise ApiError(401, "unauthorized", "認証トークンが無効です。") from exc


def require_cron_secret(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    settings: WorkerSettings = Depends(get_worker_settings),
) -> None:
    """(c) Cloud Scheduler からの呼び出しであることを共有シークレットで確認する。

    ヘッダーが無い・空のときは、比較の前に401を返す。
    """

    if not x_cron_secret:
        raise ApiError(401, "unauthorized", "X-Cron-Secret が指定されていません。")
    expected = settings.cron_secret.get_secret_value()
    if not hmac.compare_digest(x_cron_secret, expected):
        raise ApiError(401, "unauthorized", "X-Cron-Secret が一致しません。")
