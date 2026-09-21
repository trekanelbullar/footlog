"""Supabase が発行した JWT の検証（設計書 §1.2 A-X2）。

受け付けるアルゴリズムは設定（``SUPABASE_JWT_ALG``）で固定し、JWT ヘッダーの ``alg``
では分岐しない。ES256・RS256 は JWKS（PyJWKClient・キャッシュあり）、HS256 は
``SUPABASE_JWT_SECRET`` で検証する。
"""

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from jwt import PyJWKClient

from ai_hackathon_team_a.worker_settings import WorkerSettings

_LEEWAY_SECONDS = 30
_REQUIRED_ROLE = "authenticated"
_REQUIRED_AUDIENCE = "authenticated"


class AuthError(Exception):
    """JWT の検証に失敗したことを表す例外（呼び出し側で401相当に変換する）。"""


@dataclass(frozen=True)
class AuthenticatedUser:
    """検証済み JWT から取り出した本人の情報。"""

    user_id: UUID
    # Supabase の JWT には通常含まれるが、必須項目ではない（無ければ None）。
    # project_members.email（設計書 §3）の初期値など、メールアドレスが要る箇所でだけ使う。
    email: str | None = None


@lru_cache(maxsize=8)
def _cached_jwk_client(jwks_url: str) -> PyJWKClient:
    """JWKS を URL ごとにキャッシュして取得するクライアント。"""

    return PyJWKClient(jwks_url)


def verify_access_token(
    token: str,
    settings: WorkerSettings,
    *,
    jwk_client: PyJWKClient | None = None,
) -> AuthenticatedUser:
    """Supabase のアクセストークンを検証し、本人のユーザーIDを返す。

    iss・aud・exp・nbf（あれば、leeway 30秒）・role・sub(UUID) をすべて必須にし、
    1つでも欠けたり合わなかったりすれば :class:`AuthError` を投げる。
    """

    alg = settings.supabase_jwt_alg
    issuer = f"{str(settings.supabase_url).rstrip('/')}/auth/v1"

    try:
        if alg == "HS256":
            if settings.supabase_jwt_secret is None:
                raise AuthError("SUPABASE_JWT_SECRET is not configured")
            key: str | bytes = settings.supabase_jwt_secret.get_secret_value()
        else:
            jwks_url = f"{issuer}/.well-known/jwks.json"
            client = jwk_client or _cached_jwk_client(jwks_url)
            key = client.get_signing_key_from_jwt(token).key

        payload = jwt.decode(
            token,
            key=key,
            algorithms=[alg],
            issuer=issuer,
            audience=_REQUIRED_AUDIENCE,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except AuthError:
        raise
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from None

    if payload.get("role") != _REQUIRED_ROLE:
        raise AuthError("token role is not 'authenticated'")

    try:
        user_id = UUID(str(payload.get("sub")))
    except (ValueError, TypeError):
        raise AuthError("token sub is not a UUID") from None

    email = payload.get("email")
    return AuthenticatedUser(user_id=user_id, email=email if isinstance(email, str) else None)
