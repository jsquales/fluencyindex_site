import base64
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from ..db import SchwabToken, SessionLocal

SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


@dataclass
class TokenRefreshResult:
    token: SchwabToken | None
    refresh_attempted: bool
    refresh_succeeded: bool
    message: str = ""


class MissingSchwabConfigError(Exception):
    pass


class SchwabTokenExchangeError(Exception):
    pass


async def exchange_authorization_code(code: str) -> SchwabToken:
    callback_url = os.getenv("SCHWAB_CALLBACK_URL")
    if not callback_url:
        raise MissingSchwabConfigError()

    payload = await _post_token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback_url,
        }
    )
    return _store_new_token(payload)


async def refresh_access_token_if_needed() -> TokenRefreshResult:
    token = get_latest_schwab_token()

    if token is None:
        return TokenRefreshResult(
            token=None,
            refresh_attempted=False,
            refresh_succeeded=False,
            message="No Schwab token stored.",
        )

    if not _is_expired(token):
        return TokenRefreshResult(
            token=token,
            refresh_attempted=False,
            refresh_succeeded=False,
            message="Access token is still valid.",
        )

    if not token.refresh_token:
        return TokenRefreshResult(
            token=token,
            refresh_attempted=True,
            refresh_succeeded=False,
            message="Token expired; refresh token missing.",
        )

    try:
        payload = await _post_token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
            }
        )
        refreshed_token = _update_existing_token(token.id, payload, token.refresh_token)
        return TokenRefreshResult(
            token=refreshed_token,
            refresh_attempted=True,
            refresh_succeeded=True,
            message="Access token refreshed.",
        )
    except Exception:
        return TokenRefreshResult(
            token=token,
            refresh_attempted=True,
            refresh_succeeded=False,
            message="Token expired; refresh failed.",
        )


def get_latest_schwab_token() -> SchwabToken | None:
    db = SessionLocal()
    try:
        return (
            db.query(SchwabToken)
            .order_by(SchwabToken.created_at.desc())
            .first()
        )
    finally:
        db.close()


async def _post_token_request(data: dict[str, str]) -> dict:
    app_key = os.getenv("SCHWAB_APP_KEY")
    app_secret = os.getenv("SCHWAB_APP_SECRET")

    if not app_key or not app_secret:
        raise MissingSchwabConfigError()

    credentials = f"{app_key}:{app_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(SCHWAB_TOKEN_URL, headers=headers, data=data)

    if response.status_code != 200:
        raise SchwabTokenExchangeError()

    try:
        payload = response.json()
    except ValueError as exc:
        raise SchwabTokenExchangeError() from exc

    if not payload.get("access_token"):
        raise SchwabTokenExchangeError()

    return payload


def _store_new_token(payload: dict) -> SchwabToken:
    now = datetime.now(timezone.utc)
    expires_in = _safe_int(payload.get("expires_in"))
    expires_at = now + timedelta(seconds=expires_in) if expires_in is not None else None

    token = SchwabToken(
        access_token=str(payload["access_token"]),
        refresh_token=_optional_string(payload.get("refresh_token")),
        token_type=_optional_string(payload.get("token_type")),
        expires_in=expires_in,
        scope=_optional_string(payload.get("scope")),
        created_at=now,
        expires_at=expires_at,
    )

    db = SessionLocal()
    try:
        db.add(token)
        db.commit()
        db.refresh(token)
        return token
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _update_existing_token(token_id: int, payload: dict, existing_refresh_token: str) -> SchwabToken:
    now = datetime.now(timezone.utc)
    expires_in = _safe_int(payload.get("expires_in"))
    expires_at = now + timedelta(seconds=expires_in) if expires_in is not None else None

    db = SessionLocal()
    try:
        token = db.query(SchwabToken).filter(SchwabToken.id == token_id).one()
        token.access_token = str(payload["access_token"])
        token.refresh_token = _optional_string(payload.get("refresh_token")) or existing_refresh_token
        token.token_type = _optional_string(payload.get("token_type")) or token.token_type
        token.expires_in = expires_in
        token.scope = _optional_string(payload.get("scope")) or token.scope
        token.created_at = now
        token.expires_at = expires_at
        db.commit()
        db.refresh(token)
        return token
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _is_expired(token: SchwabToken) -> bool:
    expires_at = _as_aware_utc(token.expires_at)
    if expires_at is None:
        return True
    return expires_at <= datetime.now(timezone.utc)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
