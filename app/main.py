from pathlib import Path
import os
import secrets
import smtplib
import ssl
import time
from threading import Lock
from email.message import EmailMessage
from typing import Optional

from fastapi import FastAPI, Request, Form, Query, BackgroundTasks, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import text

from .db import SessionLocal, WaitlistEntry, init_db  # NEW

BASE_DIR = Path(__file__).resolve().parent  # app/

app = FastAPI()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

APP_ADS_TXT = "google.com, pub-2528199269226724, DIRECT, f08c47fec0942fa0"
ADMIN_SESSION_COOKIE = "admin_session"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_BLOCK_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 8
_login_attempts: dict[str, dict[str, object]] = {}
_login_attempts_lock = Lock()


def _admin_session_serializer() -> Optional[URLSafeTimedSerializer]:
    secret_key = os.getenv("SESSION_SECRET_KEY")
    if not secret_key:
        return None
    return URLSafeTimedSerializer(secret_key, salt="admin-session")


def create_admin_session_token(username: str) -> Optional[str]:
    serializer = _admin_session_serializer()
    if not serializer:
        return None
    return serializer.dumps({"u": username})


def verify_admin_session_token(token: str) -> bool:
    serializer = _admin_session_serializer()
    expected_username = os.getenv("ADMIN_USERNAME")
    if not serializer or not expected_username:
        return False
    try:
        payload = serializer.loads(token, max_age=60 * 60 * 24 * 7)
    except BadSignature:
        return False
    username = payload.get("u") if isinstance(payload, dict) else None
    return isinstance(username, str) and secrets.compare_digest(username, expected_username)


def require_admin(request: Request) -> bool:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if token and verify_admin_session_token(token):
        return True
    raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


def require_ingest_api_key(x_api_key: Optional[str] = Header(default=None)) -> bool:
    expected = os.getenv("INGEST_API_KEY")
    if not expected or not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


def _get_client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_login_blocked(client_ip: str) -> bool:
    now = time.time()
    with _login_attempts_lock:
        entry = _login_attempts.get(client_ip)
        if not entry:
            return False
        blocked_until = float(entry.get("blocked_until", 0.0))
        if blocked_until > now:
            return True
        if blocked_until:
            entry["blocked_until"] = 0.0
        return False


def _record_login_failure(client_ip: str) -> None:
    now = time.time()
    cutoff = now - LOGIN_WINDOW_SECONDS
    with _login_attempts_lock:
        entry = _login_attempts.setdefault(client_ip, {"fails": [], "blocked_until": 0.0})
        fails = [float(ts) for ts in entry.get("fails", []) if float(ts) >= cutoff]
        fails.append(now)
        entry["fails"] = fails
        if len(fails) >= LOGIN_MAX_FAILURES:
            entry["blocked_until"] = now + LOGIN_BLOCK_SECONDS


def _clear_login_failures(client_ip: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(client_ip, None)


def send_signup_notification_email(
    *,
    name: str,
    role: str,
    email: str,
    notes: str,
) -> bool:
    """Best-effort admin notification for new signup entries."""
    sender_email = os.getenv("OUTLOOK_EMAIL")
    sender_password = os.getenv("OUTLOOK_PASSWORD")
    admin_notify_email = os.getenv("ADMIN_NOTIFY_EMAIL")

    if not sender_email or not sender_password or not admin_notify_email:
        print("Signup notification skipped: missing OUTLOOK_EMAIL/OUTLOOK_PASSWORD/ADMIN_NOTIFY_EMAIL")
        return False

    msg = EmailMessage()
    msg["Subject"] = "New Fluency Index signup"
    msg["From"] = sender_email
    msg["To"] = admin_notify_email
    msg.set_content(
        "\n".join(
            [
                "A new waitlist signup was submitted:",
                f"Name: {name}",
                f"Role: {role}",
                f"Email: {email}",
                f"Notes: {notes.strip() if notes and notes.strip() else '(none)'}",
            ]
        )
    )

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print("Signup notification email failed:", e)
        return False


class AttemptIn(BaseModel):
    client_attempt_id: Optional[str] = Field(default=None, max_length=64)
    created_from: Optional[str] = Field(default="math_rush", max_length=64)
    session_id: int = Field(ge=1, le=2_147_483_647)
    student_id: int = Field(ge=1, le=2_147_483_647)
    question: Optional[str] = Field(default=None, max_length=2000)
    answer_given: Optional[str] = Field(default=None, max_length=2000)
    is_correct: Optional[bool] = None
    response_ms: Optional[int] = Field(default=None, ge=0, le=600_000)
    duration_seconds: Optional[int] = Field(default=None, ge=0, le=86_400)
    score: Optional[float] = Field(default=None, ge=0, le=1_000_000)


class MRSessionStartIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    client_session_id: int = Field(ge=0, le=2_147_483_647)
    mode: Optional[str] = Field(default=None, max_length=32)
    difficulty: Optional[str] = Field(default=None, max_length=32)
    count_target: Optional[int] = Field(default=None, ge=0, le=100_000)
    started_at_ms: Optional[int] = Field(default=None, ge=0, le=4_102_444_800_000)


class MRSessionEndIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    client_session_id: int = Field(ge=0, le=2_147_483_647)
    ended_at_ms: Optional[int] = Field(default=None, ge=0, le=4_102_444_800_000)
    attempted: Optional[int] = Field(default=None, ge=0, le=100_000)
    correct: Optional[int] = Field(default=None, ge=0, le=100_000)
    avg_ms: Optional[int] = Field(default=None, ge=0, le=600_000)
    duration_s: Optional[int] = Field(default=None, ge=0, le=86_400)


class MRQuestionIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    client_session_id: int = Field(ge=0, le=2_147_483_647)
    a: Optional[int] = Field(default=None, ge=0, le=10_000)
    b: Optional[int] = Field(default=None, ge=0, le=10_000)
    user_answer: Optional[int] = Field(default=None, ge=-1_000_000, le=1_000_000)
    correct: Optional[bool] = None
    elapsed_ms: Optional[int] = Field(default=None, ge=0, le=600_000)
    timestamp_ms: Optional[int] = Field(default=None, ge=0, le=4_102_444_800_000)
    client_attempt_id: Optional[str] = Field(default=None, max_length=64)


@app.on_event("startup")
def on_startup() -> None:
    """Initialize the database (create tables if needed)."""
    init_db()


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(
        "landing.html",
        {"request": request, "page_title": "Fluency Index"}
    )


@app.get("/app-ads.txt", response_class=PlainTextResponse)
async def app_ads_txt():
    return APP_ADS_TXT


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.png")


@app.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "page_title": "Sign In | Fluency Index"
        }
    )


@app.get("/signup", response_class=HTMLResponse)
async def signup_get(request: Request):
    # Show the waitlist form
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "page_title": "Get Early Access | Fluency Index"
        }
    )


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_get(request: Request):
    return templates.TemplateResponse(
        "admin_login.html",
        {
            "request": request,
            "page_title": "Admin Login | Fluency Index",
            "error": None,
        },
    )


@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    client_ip = _get_client_ip(request)
    if _is_login_blocked(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")

    expected_username = os.getenv("ADMIN_USERNAME")
    password_hash = os.getenv("ADMIN_PASSWORD_HASH")

    is_valid = (
        bool(expected_username)
        and bool(password_hash)
        and secrets.compare_digest(username, expected_username)
        and pwd_context.verify(password, password_hash)
    )

    if not is_valid:
        _record_login_failure(client_ip)
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "page_title": "Admin Login | Fluency Index",
                "error": "Invalid username or password.",
            },
            status_code=401,
        )

    _clear_login_failures(client_ip)
    token = create_admin_session_token(expected_username)
    if not token:
        return templates.TemplateResponse(
            "admin_login.html",
            {
                "request": request,
                "page_title": "Admin Login | Fluency Index",
                "error": "Admin login is not configured.",
            },
            status_code=500,
        )

    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@app.post("/admin/logout")
async def admin_logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(key=ADMIN_SESSION_COOKIE, path="/")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request, _: bool = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin_home.html",
        {
            "request": request,
            "page_title": "Admin | Fluency Index",
        },
    )


@app.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions(request: Request, _: bool = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin_sessions.html",
        {
            "request": request,
            "page_title": "Recent Math Rush Sessions",
        }
    )


@app.get("/admin/sessions/{device_id}/{client_session_id}", response_class=HTMLResponse)
async def admin_session_detail(
    request: Request,
    device_id: str,
    client_session_id: int,
    _: bool = Depends(require_admin),
):
    return templates.TemplateResponse(
        "admin_session_detail.html",
        {
            "request": request,
            "page_title": "Math Rush Session Detail",
            "device_id": device_id,
            "client_session_id": client_session_id,
        }
    )


@app.post("/signup", response_class=HTMLResponse)
async def signup_post(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(..., max_length=80),
    role: str = Form(..., max_length=80),
    email: str = Form(..., max_length=254),
    notes: str = Form("", max_length=1000),
):
    """
    Handle the Join the Pilot Waitlist form submission.

    For now, we save it to the waitlist_entries table and log it.
    """
    # Avoid logging PII payloads in production logs.
    print("New waitlist signup received")

    # Save to the database
    session = SessionLocal()
    try:
        entry = WaitlistEntry(
            name=name,
            role=role,
            email=email,
            notes=notes if notes.strip() else None,
        )
        session.add(entry)
        session.commit()
        background_tasks.add_task(
            send_signup_notification_email,
            name=name,
            role=role,
            email=email,
            notes=notes,
        )
    except Exception as e:
        session.rollback()
        print("Error saving waitlist signup:", e)
    finally:
        session.close()

    return templates.TemplateResponse(
        "signup_thanks.html",
        {
            "request": request,
            "page_title": "Thanks for Joining the Waitlist | Fluency Index",
            "name": name,
        }
    )


@app.post("/api/v1/attempts")
async def create_attempt(payload: AttemptIn, _: bool = Depends(require_ingest_api_key)):
    db = SessionLocal()
    try:
        if payload.client_attempt_id:
            existing = db.execute(
                text(
                    """
                    SELECT id
                    FROM attempts
                    WHERE client_attempt_id = :client_attempt_id
                    LIMIT 1
                    """
                ),
                {"client_attempt_id": payload.client_attempt_id},
            ).first()
            if existing:
                return {"status": "duplicate", "attempt_id": existing.id}

        inserted = db.execute(
            text(
                """
                INSERT INTO attempts (
                    session_id,
                    student_id,
                    question,
                    answer_given,
                    is_correct,
                    response_ms,
                    duration_seconds,
                    score,
                    client_attempt_id,
                    created_from
                )
                VALUES (
                    :session_id,
                    :student_id,
                    :question,
                    :answer_given,
                    :is_correct,
                    :response_ms,
                    :duration_seconds,
                    :score,
                    :client_attempt_id,
                    :created_from
                )
                RETURNING id
                """
            ),
            payload.model_dump(),
        ).first()
        db.commit()
        return {"status": "ok", "attempt_id": inserted.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/v1/mr/session/start")
async def mr_session_start(payload: MRSessionStartIn, _: bool = Depends(require_ingest_api_key)):
    db = SessionLocal()
    try:
        db.execute(
            text(
                """
                INSERT INTO mr_sessions (
                    device_id, client_session_id, mode, difficulty, count_target, started_at_ms
                )
                VALUES (
                    :device_id, :client_session_id, :mode, :difficulty, :count_target, :started_at_ms
                )
                ON CONFLICT (device_id, client_session_id)
                DO UPDATE SET
                    mode = COALESCE(EXCLUDED.mode, mr_sessions.mode),
                    difficulty = COALESCE(EXCLUDED.difficulty, mr_sessions.difficulty),
                    count_target = COALESCE(EXCLUDED.count_target, mr_sessions.count_target),
                    started_at_ms = COALESCE(EXCLUDED.started_at_ms, mr_sessions.started_at_ms)
                """
            ),
            payload.model_dump(),
        )
        db.commit()
        return {"status": "ok"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/v1/mr/session/end")
async def mr_session_end(payload: MRSessionEndIn, _: bool = Depends(require_ingest_api_key)):
    db = SessionLocal()
    try:
        db.execute(
            text(
                """
                UPDATE mr_sessions
                SET
                    ended_at_ms = :ended_at_ms,
                    attempted = :attempted,
                    correct = :correct,
                    avg_ms = :avg_ms,
                    duration_s = :duration_s
                WHERE device_id = :device_id
                  AND client_session_id = :client_session_id
                """
            ),
            payload.model_dump(),
        )
        db.commit()
        return {"status": "ok"}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/v1/mr/question")
async def mr_question(payload: MRQuestionIn, _: bool = Depends(require_ingest_api_key)):
    db = SessionLocal()
    try:
        if payload.client_attempt_id:
            existing = db.execute(
                text(
                    """
                    SELECT id
                    FROM mr_question_events
                    WHERE device_id = :device_id
                      AND client_attempt_id = :client_attempt_id
                    LIMIT 1
                    """
                ),
                {
                    "device_id": payload.device_id,
                    "client_attempt_id": payload.client_attempt_id,
                },
            ).first()
            if existing:
                return {"status": "duplicate"}

        inserted = db.execute(
            text(
                """
                INSERT INTO mr_question_events (
                    device_id,
                    client_session_id,
                    a,
                    b,
                    user_answer,
                    correct,
                    elapsed_ms,
                    timestamp_ms,
                    client_attempt_id
                )
                VALUES (
                    :device_id,
                    :client_session_id,
                    :a,
                    :b,
                    :user_answer,
                    :correct,
                    :elapsed_ms,
                    :timestamp_ms,
                    :client_attempt_id
                )
                RETURNING id
                """
            ),
            payload.model_dump(),
        ).first()
        db.commit()
        return {"status": "ok", "id": inserted.id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/v1/mr/sessions/recent")
async def mr_sessions_recent(
    _: bool = Depends(require_admin),
    device_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        sql = """
        SELECT
          s.device_id,
          s.client_session_id,
          s.mode,
          s.difficulty,
          s.started_at_ms,
          s.ended_at_ms,
          s.attempted,
          s.correct,
          s.avg_ms,
          COUNT(q.id) AS events_logged
        FROM mr_sessions s
        LEFT JOIN mr_question_events q
          ON q.device_id = s.device_id
         AND q.client_session_id = s.client_session_id
        """
        params = {"limit": limit}
        if device_id:
            sql += "\nWHERE s.device_id = :device_id\n"
            params["device_id"] = device_id
        sql += """
        GROUP BY
          s.device_id, s.client_session_id, s.mode, s.difficulty,
          s.started_at_ms, s.ended_at_ms, s.attempted, s.correct, s.avg_ms
        ORDER BY s.started_at_ms DESC
        LIMIT :limit;
        """
        rows = db.execute(text(sql), params).fetchall()
        return [dict(row._mapping) for row in rows]
    finally:
        db.close()


@app.get("/api/v1/mr/session/events")
async def mr_session_events(
    device_id: str,
    client_session_id: int,
    limit: int = Query(default=200, ge=1, le=200),
    _: bool = Depends(require_admin),
):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                  id,
                  device_id,
                  client_session_id,
                  a,
                  b,
                  user_answer,
                  correct,
                  elapsed_ms,
                  timestamp_ms,
                  client_attempt_id
                FROM mr_question_events
                WHERE device_id = :device_id
                  AND client_session_id = :client_session_id
                ORDER BY timestamp_ms ASC NULLS LAST, id ASC
                LIMIT :limit;
                """
            ),
            {
                "device_id": device_id,
                "client_session_id": client_session_id,
                "limit": limit,
            },
        ).fetchall()
        return [dict(row._mapping) for row in rows]
    finally:
        db.close()
