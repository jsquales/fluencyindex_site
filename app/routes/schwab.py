import base64
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from ..db import SchwabToken, SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schwab", tags=["schwab"])

SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"


def _simple_page(title: str, message: str) -> HTMLResponse:
    html = f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{title}</title>
        <style>
          body {{
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f6f7f9;
            color: #17202a;
          }}
          main {{
            max-width: 560px;
            margin: 12vh auto;
            padding: 32px;
            background: #ffffff;
            border: 1px solid #d9dee7;
            border-radius: 8px;
            text-align: center;
          }}
          h1 {{
            margin: 0 0 12px;
            font-size: 24px;
          }}
          p {{
            margin: 0;
            line-height: 1.5;
          }}
        </style>
      </head>
      <body>
        <main>
          <h1>{title}</h1>
          <p>{message}</p>
        </main>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/callback", response_class=HTMLResponse)
async def schwab_callback(
    code: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
) -> HTMLResponse:
    """Receive Schwab OAuth redirects and persist tokens without exposing sensitive values."""
    if error:
        logger.warning("Schwab OAuth callback returned an error. Sensitive query values were not logged.")
        return _simple_page(
            "Schwab Authorization Failed",
            "Schwab authorization failed. Please return to the app and try again.",
        )

    if not code:
        logger.info("Schwab OAuth callback received without an authorization code.")
        return _simple_page(
            "No Authorization Code",
            "No authorization code received.",
        )

    try:
        token_payload = await _exchange_code_for_tokens(code)
        _store_tokens(token_payload)
    except MissingSchwabConfigError:
        logger.error("Schwab OAuth callback could not exchange code because server configuration is incomplete.")
        return _simple_page(
            "Schwab Configuration Missing",
            "Schwab authorization was received, but the server is missing required Schwab configuration.",
        )
    except SchwabTokenExchangeError:
        logger.error("Schwab OAuth token exchange failed. Token response details were not logged.")
        return _simple_page(
            "Schwab Token Exchange Failed",
            "Schwab authorization was received, but the token exchange failed. Please try again later.",
        )
    except Exception:
        logger.exception("Unexpected Schwab OAuth callback failure. Sensitive values were not logged.")
        return _simple_page(
            "Schwab Authorization Error",
            "Schwab authorization was received, but the server could not finish setup.",
        )

    logger.info("Schwab OAuth tokens stored successfully. Token values were not logged.")
    return _simple_page(
        "Schwab Authorization Complete",
        "Schwab authorization was completed and stored securely. You may close this window.",
    )


async def _exchange_code_for_tokens(code: str) -> dict:
    app_key = os.getenv("SCHWAB_APP_KEY")
    app_secret = os.getenv("SCHWAB_APP_SECRET")
    callback_url = os.getenv("SCHWAB_CALLBACK_URL")

    if not app_key or not app_secret or not callback_url:
        raise MissingSchwabConfigError()

    credentials = f"{app_key}:{app_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(SCHWAB_TOKEN_URL, headers=headers, data=data)

    if response.status_code != 200:
        raise SchwabTokenExchangeError()

    payload = response.json()
    if not payload.get("access_token"):
        raise SchwabTokenExchangeError()

    return payload


def _store_tokens(payload: dict) -> None:
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
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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


class MissingSchwabConfigError(Exception):
    pass


class SchwabTokenExchangeError(Exception):
    pass
