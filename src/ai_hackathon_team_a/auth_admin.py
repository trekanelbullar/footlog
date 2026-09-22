"""Supabase Auth の管理API（サービスロール）。設計書 §2.1 W6、S10：Supabase 固有の
処理はここに閉じ込める。

メンバーの追加（W6）は「登録済みユーザーのメールアドレス」を指定する仕様のため、
ここではメールアドレスから ``user_id`` を引く1関数だけを持つ。
"""

import httpx

from ai_hackathon_team_a.worker_settings import WorkerSettings


class AuthAdminError(RuntimeError):
    """Supabase Auth の管理APIの呼び出しが失敗したことを表す。"""


def find_user_id_by_email(
    email: str,
    *,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """登録済みユーザーのメールアドレスから ``user_id`` を引く。未登録なら ``None``。"""

    key = settings.supabase_service_role_key.get_secret_value()
    base_url = f"{str(settings.supabase_url).rstrip('/')}/auth/v1"
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {key}", "apikey": key},
        transport=transport,
        timeout=30.0,
    ) as client:
        response = client.get("/admin/users", params={"email": email})

    if response.status_code >= 400:
        raise AuthAdminError(f"Supabase Auth admin lookup failed: {response.status_code}")

    target = email.strip().lower()
    for user in response.json().get("users", []):
        if str(user.get("email", "")).strip().lower() == target:
            return str(user["id"])
    return None
