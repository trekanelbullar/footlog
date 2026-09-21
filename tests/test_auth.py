import base64
import hashlib
import hmac as hmac_lib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from ai_hackathon_team_a.auth import AuthError, verify_access_token
from ai_hackathon_team_a.worker_settings import WorkerSettings

_ISSUER = "https://example.supabase.co/auth/v1"
_WORKER_SECRET = "w" * 32
_CRON_SECRET = "c" * 32


def _base_worker_kwargs(**overrides: object) -> dict:
    kwargs: dict = {
        "_env_file": None,
        "database_url": "postgresql://localhost/decision_trace",
        "supabase_url": "https://example.supabase.co",
        "supabase_service_role_key": "service-role-key",
        "supabase_jwt_alg": "HS256",
        "supabase_jwt_secret": "hs256-secret",
        "resend_api_key": "resend-key",
        "mail_from": "noreply@example.test",
        "app_base_url": "https://app.example.test",
        "worker_shared_secret": _WORKER_SECRET,
        "cron_secret": _CRON_SECRET,
        "daily_cost_limit_usd": 5.0,
        "system_alert_email": "alert@example.test",
    }
    kwargs.update(overrides)
    return kwargs


def _hs256_settings(**overrides: object) -> WorkerSettings:
    return WorkerSettings(**_base_worker_kwargs(**overrides))  # type: ignore[arg-type]


def _es256_settings(**overrides: object) -> WorkerSettings:
    kwargs = _base_worker_kwargs(supabase_jwt_alg="ES256", supabase_jwt_secret=None)
    kwargs.update(overrides)
    return WorkerSettings(**kwargs)  # type: ignore[arg-type]


def _payload(**overrides: object) -> dict:
    now = datetime.now(tz=UTC)
    payload = {
        "iss": _ISSUER,
        "aud": "authenticated",
        "role": "authenticated",
        "sub": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    payload.update(overrides)
    return payload


class _StaticJwkClient:
    def __init__(self, key: object) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=self._key)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ---- HS256 ----------------------------------------------------------------


def test_hs256_valid_token_returns_user_id() -> None:
    settings = _hs256_settings()
    payload = _payload()
    token = jwt.encode(payload, "hs256-secret", algorithm="HS256")

    user = verify_access_token(token, settings)

    assert str(user.user_id) == payload["sub"]


def test_hs256_wrong_secret_is_rejected() -> None:
    settings = _hs256_settings()
    token = jwt.encode(_payload(), "wrong-secret", algorithm="HS256")

    with pytest.raises(AuthError):
        verify_access_token(token, settings)


def test_hs256_expired_token_is_rejected() -> None:
    settings = _hs256_settings()
    now = datetime.now(tz=UTC)
    payload = _payload(
        iat=int((now - timedelta(hours=2)).timestamp()),
        exp=int((now - timedelta(hours=1)).timestamp()),
    )
    token = jwt.encode(payload, "hs256-secret", algorithm="HS256")

    with pytest.raises(AuthError):
        verify_access_token(token, settings)


def test_hs256_wrong_audience_is_rejected() -> None:
    settings = _hs256_settings()
    token = jwt.encode(_payload(aud="not-authenticated"), "hs256-secret", algorithm="HS256")

    with pytest.raises(AuthError):
        verify_access_token(token, settings)


def test_hs256_wrong_issuer_is_rejected() -> None:
    settings = _hs256_settings()
    token = jwt.encode(
        _payload(iss="https://evil.example/auth/v1"), "hs256-secret", algorithm="HS256"
    )

    with pytest.raises(AuthError):
        verify_access_token(token, settings)


def test_wrong_role_is_rejected() -> None:
    settings = _hs256_settings()
    token = jwt.encode(_payload(role="anon"), "hs256-secret", algorithm="HS256")

    with pytest.raises(AuthError):
        verify_access_token(token, settings)


def test_non_uuid_sub_is_rejected() -> None:
    settings = _hs256_settings()
    token = jwt.encode(_payload(sub="not-a-uuid"), "hs256-secret", algorithm="HS256")

    with pytest.raises(AuthError):
        verify_access_token(token, settings)


def test_alg_none_is_rejected() -> None:
    settings = _hs256_settings()
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(_payload()).encode())
    token = f"{header}.{body}."

    with pytest.raises(AuthError):
        verify_access_token(token, settings)


# ---- ES256 / JWKS -----------------------------------------------------------


def _generate_ec_keypair() -> tuple[bytes, bytes]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public_pem = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def test_es256_valid_token_returns_user_id_via_injected_jwk_client() -> None:
    settings = _es256_settings()
    private_pem, public_pem = _generate_ec_keypair()
    payload = _payload()
    token = jwt.encode(payload, private_pem, algorithm="ES256")

    user = verify_access_token(token, settings, jwk_client=_StaticJwkClient(public_pem))

    assert str(user.user_id) == payload["sub"]


def test_es256_header_alg_downgrade_to_hs256_is_rejected() -> None:
    """設定は ES256 固定なので、ヘッダーの alg を HS256 に偽装しても拒否される。"""

    settings = _es256_settings()
    _, public_pem = _generate_ec_keypair()

    # 典型的な alg confusion 攻撃：ES256 の公開鍵を HMAC の鍵として使い、
    # ヘッダーの alg を HS256 と偽る。PyJWT の jwt.encode は非対称鍵を HMAC の鍵に
    # 使おうとすると拒否するため、ここでは署名を手組みして偽装トークンを作る。
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(_payload()).encode())
    signing_input = f"{header}.{body}".encode()
    signature = hmac_lib.new(public_pem, signing_input, hashlib.sha256).digest()
    forged_token = f"{header}.{body}.{_b64url(signature)}"

    with pytest.raises(AuthError):
        verify_access_token(forged_token, settings, jwk_client=_StaticJwkClient(public_pem))
