import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schwab", tags=["schwab"])


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
    session: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
) -> HTMLResponse:
    """Receive Schwab OAuth redirects without exchanging or exposing tokens yet."""
    if error:
        logger.warning("Schwab OAuth callback returned an error. No sensitive query values logged.")
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

    # Do not log the authorization code, session, or any future token values.
    logger.info("Schwab OAuth authorization code received. Token exchange is not implemented yet.")
    return _simple_page(
        "Schwab Authorization Received",
        "Schwab authorization code received. You may close this window.",
    )
