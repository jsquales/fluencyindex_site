from pathlib import Path
import os
import random
import re
import secrets
import smtplib
import ssl
import time
from datetime import datetime, timezone
from threading import Lock
from email.message import EmailMessage
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request, Form, Query, BackgroundTasks, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import text

from .db import SessionLocal, WaitlistEntry, init_db  # NEW
from .routes.game24 import router as game24_router
from .routes.schwab import router as schwab_router
from .services.game24_service import get_game24_options_response
from .services.schwab_token_service import get_latest_schwab_token, refresh_access_token_if_needed
from .services.testing import CheckinError, start_session

BASE_DIR = Path(__file__).resolve().parent  # app/

app = FastAPI()
app.include_router(game24_router)
app.include_router(schwab_router)

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
TEST_FACTOR_MIN = 1
TEST_FACTOR_MAX = 12
TEST_TOTAL_QUESTIONS = 10
SCHWAB_QUOTE_TEST_ENDPOINT = "https://api.schwabapi.com/marketdata/v1/quotes"


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


@app.get("/24-challenge", response_class=HTMLResponse)
async def game24_challenge_page(request: Request):
    db = SessionLocal()
    try:
        options = get_game24_options_response(db)
        return templates.TemplateResponse(
            "game24_challenge.html",
            {
                "request": request,
                "page_title": "24 Challenge | Fluency Index",
                "game24_options": options,
            },
        )
    finally:
        db.close()


@app.get("/test/checkin", response_class=HTMLResponse)
async def test_checkin_page(request: Request, error: Optional[str] = None):
    # TODO: Pilot flow uses /admin/test/checkin only.
    raise HTTPException(status_code=404, detail="Not Found")


@app.post("/test/checkin/start")
async def test_checkin_start(student_id: str = Form(...)):
    # TODO: Pilot flow uses /admin/test/checkin/start only.
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/test/run/{session_id}", response_class=HTMLResponse)
async def run_test_page(session_id: int, request: Request, _: bool = Depends(require_admin)):
    db = SessionLocal()
    try:
        session_row = db.execute(
            text(
                """
                SELECT id, class_id, teacher_id, student_identifier, status, started_at
                FROM sessions
                WHERE id = :session_id
                LIMIT 1
                """
            ),
            {"session_id": session_id},
        ).first()
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found.")

        attempt_count = int(
            db.execute(
                text("SELECT COUNT(*) AS c FROM attempts WHERE session_id = :session_id"),
                {"session_id": session_id},
            ).scalar_one()
        )
        if attempt_count >= TEST_TOTAL_QUESTIONS:
            return RedirectResponse(url=f"/test/results/{session_id}", status_code=303)

        existing_questions = {
            str(row.question)
            for row in db.execute(
                text(
                    """
                    SELECT question
                    FROM attempts
                    WHERE session_id = :session_id
                    """
                ),
                {"session_id": session_id},
            ).fetchall()
            if row.question
        }
    finally:
        db.close()

    a = random.randint(TEST_FACTOR_MIN, TEST_FACTOR_MAX)
    b = random.randint(TEST_FACTOR_MIN, TEST_FACTOR_MAX)
    question_text = f"{a} x {b}"
    for _ in range(24):
        if question_text not in existing_questions:
            break
        a = random.randint(TEST_FACTOR_MIN, TEST_FACTOR_MAX)
        b = random.randint(TEST_FACTOR_MIN, TEST_FACTOR_MAX)
        question_text = f"{a} x {b}"

    return templates.TemplateResponse(
        "test_run.html",
        {
            "request": request,
            "page_title": "Fluency Test | Fluency Index",
            "session_id": session_id,
            "question_number": attempt_count + 1,
            "question_total": TEST_TOTAL_QUESTIONS,
            "a": a,
            "b": b,
        },
    )


@app.post("/test/run/{session_id}/answer")
async def submit_test_answer(
    session_id: int,
    a: int = Form(...),
    b: int = Form(...),
    answer: str = Form(""),
    elapsed_ms: Optional[int] = Form(default=None),
    _: bool = Depends(require_admin),
):
    db = SessionLocal()
    try:
        session_row = db.execute(
            text(
                """
                SELECT id, class_id, student_identifier
                FROM sessions
                WHERE id = :session_id
                LIMIT 1
                """
            ),
            {"session_id": session_id},
        ).first()
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found.")

        student_row = db.execute(
            text(
                """
                SELECT id
                FROM students
                WHERE class_id = :class_id
                  AND student_identifier = :student_identifier
                LIMIT 1
                """
            ),
            {
                "class_id": int(session_row.class_id),
                "student_identifier": str(session_row.student_identifier or ""),
            },
        ).first()
        if not student_row:
            raise HTTPException(status_code=400, detail="Student record not found for session.")

        normalized_answer = answer.strip()
        answer_int: Optional[int] = None
        if normalized_answer:
            try:
                answer_int = int(normalized_answer)
            except ValueError:
                answer_int = None

        correct_answer = int(a) * int(b)
        is_correct = answer_int is not None and answer_int == correct_answer
        bounded_elapsed_ms = None
        if elapsed_ms is not None:
            bounded_elapsed_ms = max(0, min(int(elapsed_ms), 60_000))

        db.execute(
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
                    :created_from
                )
                """
            ),
            {
                "session_id": session_id,
                "student_id": int(student_row.id),
                "question": f"{a} x {b}",
                "answer_given": normalized_answer if normalized_answer else None,
                "is_correct": is_correct,
                "response_ms": bounded_elapsed_ms,
                "duration_seconds": (bounded_elapsed_ms // 1000) if bounded_elapsed_ms is not None else None,
                "score": 1.0 if is_correct else 0.0,
                "created_from": "pilot_web_runner",
            },
        )
        db.commit()

        attempt_count = int(
            db.execute(
                text("SELECT COUNT(*) AS c FROM attempts WHERE session_id = :session_id"),
                {"session_id": session_id},
            ).scalar_one()
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if attempt_count >= TEST_TOTAL_QUESTIONS:
        return RedirectResponse(url=f"/test/results/{session_id}", status_code=303)
    return RedirectResponse(url=f"/test/run/{session_id}", status_code=303)


@app.get("/test/results/{session_id}", response_class=HTMLResponse)
async def test_results_page(session_id: int, request: Request, _: bool = Depends(require_admin)):
    db = SessionLocal()
    try:
        session_row = db.execute(
            text(
                """
                SELECT
                    s.id AS session_id,
                    s.status AS status,
                    s.started_at AS started_at,
                    s.teacher_id AS teacher_id,
                    s.student_identifier AS student_identifier,
                    c.room_code AS room_code
                FROM sessions s
                LEFT JOIN classes c ON c.id = s.class_id
                WHERE s.id = :session_id
                LIMIT 1
                """
            ),
            {"session_id": session_id},
        ).first()
        if not session_row:
            raise HTTPException(status_code=404, detail="Session not found.")

        attempts = db.execute(
            text(
                """
                SELECT question, answer_given, is_correct, response_ms
                FROM attempts
                WHERE session_id = :session_id
                ORDER BY id ASC
                """
            ),
            {"session_id": session_id},
        ).fetchall()
    finally:
        db.close()

    total = len(attempts)
    correct = sum(1 for r in attempts if bool(r.is_correct))
    accuracy_pct = round((correct / total) * 100, 1) if total else 0.0
    response_values = [int(r.response_ms) for r in attempts if r.response_ms is not None]
    avg_ms = round(sum(response_values) / len(response_values), 1) if response_values else None

    return templates.TemplateResponse(
        "test_results.html",
        {
            "request": request,
            "page_title": "Test Results | Fluency Index",
            "session": dict(session_row._mapping),
            "attempts": [dict(r._mapping) for r in attempts],
            "total": total,
            "correct": correct,
            "accuracy_pct": accuracy_pct,
            "avg_ms": avg_ms,
        },
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


def _table_columns(db, table_name: str) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(r.column_name) for r in rows}


def _ensure_pilot_roster_tables(db) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS classes (
                id BIGSERIAL PRIMARY KEY,
                room_code VARCHAR(16) UNIQUE,
                teacher_id INTEGER NOT NULL
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS students (
                id BIGSERIAL PRIMARY KEY,
                student_identifier VARCHAR(128) UNIQUE,
                class_id BIGINT NOT NULL REFERENCES classes(id),
                is_active BOOLEAN NOT NULL DEFAULT true
            )
            """
        )
    )

    class_columns = _table_columns(db, "classes")
    if "room_code" not in class_columns:
        db.execute(text("ALTER TABLE classes ADD COLUMN room_code VARCHAR(16)"))

    student_columns = _table_columns(db, "students")
    if "is_active" not in student_columns:
        db.execute(text("ALTER TABLE students ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true"))


def _ensure_teacher_one(db) -> None:
    user_columns = _table_columns(db, "users")
    if not user_columns:
        return
    exists = db.execute(text("SELECT id FROM users WHERE id = 1 LIMIT 1")).first()
    if exists:
        return

    insert_cols = ["id"]
    insert_vals = [":id"]
    params = {"id": 1}
    if "full_name" in user_columns:
        insert_cols.append("full_name")
        insert_vals.append(":full_name")
        params["full_name"] = "Pilot Teacher"
    if "email" in user_columns:
        insert_cols.append("email")
        insert_vals.append(":email")
        params["email"] = "pilot-teacher@local"
    if "role" in user_columns:
        insert_cols.append("role")
        insert_vals.append(":role")
        params["role"] = "teacher"
    if "is_active" in user_columns:
        insert_cols.append("is_active")
        insert_vals.append(":is_active")
        params["is_active"] = True

    db.execute(
        text(
            f"""
            INSERT INTO users ({", ".join(insert_cols)})
            VALUES ({", ".join(insert_vals)})
            """
        ),
        params,
    )


def _load_recent_roster_students(limit: int = 10) -> list[dict]:
    db = SessionLocal()
    try:
        _ensure_pilot_roster_tables(db)
        rows = db.execute(
            text(
                """
                SELECT
                    c.room_code,
                    s.student_identifier
                FROM students s
                JOIN classes c ON c.id = s.class_id
                ORDER BY s.id DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        db.close()


@app.get("/admin/roster", response_class=HTMLResponse)
async def admin_roster_get(
    request: Request,
    error: Optional[str] = None,
    success: Optional[str] = None,
    room: str = "",
    student_id: str = "",
    _: bool = Depends(require_admin),
):
    recent_students: list[dict] = []
    table_error: Optional[str] = None
    try:
        recent_students = _load_recent_roster_students(limit=10)
    except Exception:
        table_error = "Roster tables are missing or not writable."

    return templates.TemplateResponse(
        "admin_roster.html",
        {
            "request": request,
            "page_title": "Quick Add Student | Fluency Index",
            "error": error or "",
            "success": success or "",
            "room": room,
            "student_id": student_id,
            "table_error": table_error,
            "recent_students": recent_students,
        },
    )


@app.post("/admin/roster/add", response_class=HTMLResponse)
async def admin_roster_add(
    request: Request,
    room: str = Form(...),
    student_id: str = Form(...),
    _: bool = Depends(require_admin),
):
    normalized_room = room.strip().upper()
    normalized_student_id = student_id.strip()

    if not re.fullmatch(r"[A-Za-z][0-9]{2}", normalized_room):
        return RedirectResponse(
            url=f"/admin/roster?error={quote('Room must match format A01.')}&student_id={quote(normalized_student_id)}",
            status_code=303,
        )
    if not re.fullmatch(r"[0-9]{6}", normalized_student_id):
        return RedirectResponse(
            url=f"/admin/roster?error={quote('Student ID must be exactly 6 digits.')}&room={quote(normalized_room)}",
            status_code=303,
        )

    db = SessionLocal()
    try:
        _ensure_pilot_roster_tables(db)
        _ensure_teacher_one(db)
        teacher_id = 1
        class_columns = _table_columns(db, "classes")
        student_columns = _table_columns(db, "students")

        class_row = db.execute(
            text(
                """
                SELECT id
                FROM classes
                WHERE room_code = :room_code
                LIMIT 1
                """
            ),
            {"room_code": normalized_room},
        ).first()
        if class_row:
            class_id = int(class_row.id)
        else:
            if "name" in class_columns:
                inserted_class = db.execute(
                    text(
                        """
                        INSERT INTO classes (teacher_id, name, room_code)
                        VALUES (:teacher_id, :name, :room_code)
                        RETURNING id
                        """
                    ),
                    {"teacher_id": teacher_id, "name": normalized_room, "room_code": normalized_room},
                ).first()
            else:
                inserted_class = db.execute(
                    text(
                        """
                        INSERT INTO classes (teacher_id, room_code)
                        VALUES (:teacher_id, :room_code)
                        RETURNING id
                        """
                    ),
                    {"teacher_id": teacher_id, "room_code": normalized_room},
                ).first()
            class_id = int(inserted_class.id)

        existing_student = db.execute(
            text(
                """
                SELECT id
                FROM students
                WHERE student_identifier = :student_identifier
                LIMIT 1
                """
            ),
            {"student_identifier": normalized_student_id},
        ).first()
        if existing_student:
            db.rollback()
            return RedirectResponse(
                url=f"/admin/roster?error={quote('Student already exists.')}&room={quote(normalized_room)}",
                status_code=303,
            )

        if "first_name" in student_columns and "last_name" in student_columns:
            db.execute(
                text(
                    """
                    INSERT INTO students (class_id, student_identifier, first_name, last_name, is_active)
                    VALUES (:class_id, :student_identifier, :first_name, :last_name, :is_active)
                    """
                ),
                {
                    "class_id": class_id,
                    "student_identifier": normalized_student_id,
                    "first_name": "Pilot",
                    "last_name": normalized_student_id,
                    "is_active": True,
                },
            )
        else:
            db.execute(
                text(
                    """
                    INSERT INTO students (class_id, student_identifier, is_active)
                    VALUES (:class_id, :student_identifier, :is_active)
                    """
                ),
                {
                    "class_id": class_id,
                    "student_identifier": normalized_student_id,
                    "is_active": True,
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        return templates.TemplateResponse(
            "admin_roster.html",
            {
                "request": request,
                "page_title": "Quick Add Student | Fluency Index",
                "error": "Roster setup failed. Check table permissions/schema and retry.",
                "success": "",
                "room": normalized_room,
                "student_id": normalized_student_id,
                "table_error": None,
                "recent_students": [],
            },
            status_code=500,
        )
    finally:
        db.close()

    return RedirectResponse(
        url=f"/admin/roster?success={quote('Student added.')}&room={quote(normalized_room)}",
        status_code=303,
    )


@app.get("/admin/test/checkin", response_class=HTMLResponse)
async def admin_test_checkin_page(
    request: Request,
    error: Optional[str] = None,
    _: bool = Depends(require_admin),
):
    return templates.TemplateResponse(
        "checkin.html",
        {
            "request": request,
            "page_title": "Student Check-In | Fluency Index",
            "error": error or "",
        },
    )


@app.post("/admin/test/checkin/start")
async def admin_test_checkin_start(
    student_id: str = Form(...),
    _: bool = Depends(require_admin),
):
    try:
        session_id = start_session(student_id)
        return RedirectResponse(url=f"/test/run/{session_id}", status_code=303)
    except CheckinError as e:
        return RedirectResponse(url=f"/admin/test/checkin?error={quote(str(e))}", status_code=303)


@app.get("/admin/test/session/{session_id}", response_class=HTMLResponse)
async def admin_test_session_verify(
    request: Request,
    session_id: int,
    _: bool = Depends(require_admin),
):
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT
                    s.id AS session_id,
                    c.room_code AS room_code,
                    s.teacher_id AS teacher_id,
                    s.started_at AS started_at,
                    s.status AS status,
                    s.student_identifier AS student_identifier
                FROM sessions s
                LEFT JOIN classes c ON c.id = s.class_id
                WHERE s.id = :session_id
                LIMIT 1
                """
            ),
            {"session_id": session_id},
        ).first()
    finally:
        db.close()

    if not row:
        raise HTTPException(status_code=404, detail="Session not found.")

    return templates.TemplateResponse(
        "admin_test_session_verify.html",
        {
            "request": request,
            "page_title": "Pilot Session Verify | Fluency Index",
            "session": dict(row._mapping),
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


@app.get("/admin/schwab/token", response_class=HTMLResponse)
async def admin_schwab_token_status(_: bool = Depends(require_admin)):
    return _render_schwab_token_status()


@app.get("/admin/schwab-status", response_class=HTMLResponse)
async def admin_schwab_status(_: bool = Depends(require_admin)):
    return _render_schwab_token_status()


@app.get("/admin/schwab-statu", response_class=HTMLResponse)
async def admin_schwab_status_typo(_: bool = Depends(require_admin)):
    return RedirectResponse(url="/admin/schwab-status", status_code=303)


@app.get("/admin/schwab-api-test", response_class=HTMLResponse)
async def admin_schwab_api_test(_: bool = Depends(require_admin)):
    refresh_result = await refresh_access_token_if_needed()
    token = refresh_result.token

    if not token:
        return HTMLResponse(
            f"""
            <!doctype html>
            <html lang="en">
              <head><meta charset="utf-8"><title>Schwab API Test</title></head>
              <body>
                <h1>Schwab API Test</h1>
                <p>No Schwab token stored.</p>
                <p>Token refresh attempted: {str(refresh_result.refresh_attempted).lower()}</p>
                <p>Token refresh succeeded: {str(refresh_result.refresh_succeeded).lower()}</p>
                <p>API request: skipped</p>
                <p>HTTP status: Unavailable</p>
              </body>
            </html>
            """
        )

    expires_at = _as_aware_utc(token.expires_at)
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        return HTMLResponse(
            f"""
            <!doctype html>
            <html lang="en">
              <head><meta charset="utf-8"><title>Schwab API Test</title></head>
              <body>
                <h1>Schwab API Test</h1>
                <p>Token expired; refresh needed.</p>
                <p>Token refresh attempted: {str(refresh_result.refresh_attempted).lower()}</p>
                <p>Token refresh succeeded: {str(refresh_result.refresh_succeeded).lower()}</p>
                <p>API request: skipped</p>
                <p>HTTP status: Unavailable</p>
              </body>
            </html>
            """
        )

    api_request_status = "failed"
    http_status = "Unavailable"
    last_price = "Unavailable"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                SCHWAB_QUOTE_TEST_ENDPOINT,
                params={"symbols": "SPY", "fields": "quote"},
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token.access_token}",
                },
            )
        http_status = str(response.status_code)
        if 200 <= response.status_code < 300:
            api_request_status = "succeeded"
            last_price = _extract_quote_last_price(response.json(), "SPY")
    except Exception:
        api_request_status = "failed"

    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="en">
          <head><meta charset="utf-8"><title>Schwab API Test</title></head>
          <body>
            <h1>Schwab API Test</h1>
            <p>Token refresh attempted: {str(refresh_result.refresh_attempted).lower()}</p>
            <p>Token refresh succeeded: {str(refresh_result.refresh_succeeded).lower()}</p>
            <p>API request: {api_request_status}</p>
            <p>HTTP status: {http_status}</p>
            <p>Endpoint used: {SCHWAB_QUOTE_TEST_ENDPOINT}</p>
            <p>Symbol: SPY</p>
            <p>Last price: {last_price}</p>
          </body>
        </html>
        """
    )


def _render_schwab_token_status() -> HTMLResponse:
    token = get_latest_schwab_token()

    if not token:
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="en">
              <head><meta charset="utf-8"><title>Schwab Token Status</title></head>
              <body>
                <h1>Schwab Token Status</h1>
                <p>No token stored</p>
                <p>Token exists: false</p>
              </body>
            </html>
            """
        )

    now = datetime.now(timezone.utc)
    expires_at = _as_aware_utc(token.expires_at)
    is_expired = expires_at is not None and expires_at <= now

    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="en">
          <head><meta charset="utf-8"><title>Schwab Token Status</title></head>
          <body>
            <h1>Schwab Token Status</h1>
            <p>Token exists: true</p>
            <p>Created at: {token.created_at}</p>
            <p>Expires at: {token.expires_at or "Unknown"}</p>
            <p>Expired: {str(is_expired).lower()}</p>
          </body>
        </html>
        """
    )


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _extract_quote_last_price(payload: object, symbol: str) -> str:
    if not isinstance(payload, dict):
        return "Unavailable"

    quote_data = payload.get(symbol) or payload.get(symbol.upper()) or payload.get(symbol.lower())
    if not isinstance(quote_data, dict):
        return "Unavailable"

    quote = quote_data.get("quote")
    if not isinstance(quote, dict):
        return "Unavailable"

    for key in ("lastPrice", "last", "mark"):
        value = quote.get(key)
        if value is not None:
            return str(value)

    return "Unavailable"


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


@app.get("/admin/test", response_class=HTMLResponse)
async def admin_test_get(request: Request, _: bool = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin_test_start.html",
        {
            "request": request,
            "page_title": "Start Student Test | Fluency Index",
            "error": None,
            "student_id": "",
        },
    )


@app.post("/admin/test/start")
async def admin_test_start(
    request: Request,
    student_id: str = Form(...),
    _: bool = Depends(require_admin),
):
    normalized_student_id = student_id.strip()
    if len(normalized_student_id) < 2 or len(normalized_student_id) > 128:
        return templates.TemplateResponse(
            "admin_test_start.html",
            {
                "request": request,
                "page_title": "Start Student Test | Fluency Index",
                "error": "Student ID must be between 2 and 128 characters.",
                "student_id": normalized_student_id,
            },
            status_code=400,
        )

    db = SessionLocal()
    try:
        inserted = db.execute(
            text(
                """
                INSERT INTO admin_test_sessions (student_id, status)
                VALUES (:student_id, 'started')
                RETURNING id;
                """
            ),
            {"student_id": normalized_student_id},
        ).first()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if not inserted:
        raise HTTPException(status_code=500, detail="Failed to create test session.")

    return RedirectResponse(url=f"/admin/test/{inserted.id}", status_code=303)


@app.get("/admin/test/{session_id}", response_class=HTMLResponse)
async def admin_test_session_detail(
    request: Request,
    session_id: int,
    _: bool = Depends(require_admin),
):
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT id, student_id, status, created_at
                FROM admin_test_sessions
                WHERE id = :session_id
                LIMIT 1;
                """
            ),
            {"session_id": session_id},
        ).first()
    finally:
        db.close()

    if not row:
        raise HTTPException(status_code=404, detail="Test session not found.")

    return templates.TemplateResponse(
        "admin_test_session_detail.html",
        {
            "request": request,
            "page_title": "Student Test Session | Fluency Index",
            "session": dict(row._mapping),
        },
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
