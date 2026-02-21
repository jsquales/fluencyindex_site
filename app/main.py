from pathlib import Path
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text

from .db import SessionLocal, WaitlistEntry, init_db  # NEW

BASE_DIR = Path(__file__).resolve().parent  # app/

app = FastAPI()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

APP_ADS_TXT = "google.com, pub-2528199269226724, DIRECT, f08c47fec0942fa0"


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
    client_attempt_id: Optional[str] = None
    created_from: Optional[str] = "math_rush"
    session_id: int
    student_id: int
    question: Optional[str] = None
    answer_given: Optional[str] = None
    is_correct: Optional[bool] = None
    response_ms: Optional[int] = None
    duration_seconds: Optional[int] = None
    score: Optional[float] = None


class MRSessionStartIn(BaseModel):
    device_id: str
    client_session_id: int
    mode: Optional[str] = None
    difficulty: Optional[str] = None
    count_target: Optional[int] = None
    started_at_ms: Optional[int] = None


class MRSessionEndIn(BaseModel):
    device_id: str
    client_session_id: int
    ended_at_ms: Optional[int] = None
    attempted: Optional[int] = None
    correct: Optional[int] = None
    avg_ms: Optional[int] = None
    duration_s: Optional[int] = None


class MRQuestionIn(BaseModel):
    device_id: str
    client_session_id: int
    a: Optional[int] = None
    b: Optional[int] = None
    user_answer: Optional[int] = None
    correct: Optional[bool] = None
    elapsed_ms: Optional[int] = None
    timestamp_ms: Optional[int] = None
    client_attempt_id: Optional[str] = None


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


@app.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions(request: Request):
    return templates.TemplateResponse(
        "admin_sessions.html",
        {
            "request": request,
            "page_title": "Recent Math Rush Sessions",
        }
    )


@app.get("/admin/sessions/{device_id}/{client_session_id}", response_class=HTMLResponse)
async def admin_session_detail(request: Request, device_id: str, client_session_id: int):
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
    name: str = Form(...),
    role: str = Form(...),
    email: str = Form(...),
    notes: str = Form(""),
):
    """
    Handle the Join the Pilot Waitlist form submission.

    For now, we save it to the waitlist_entries table and log it.
    """
    print("New waitlist signup:", {"name": name, "role": role, "email": email, "notes": notes})

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
        send_signup_notification_email(name=name, role=role, email=email, notes=notes)
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
async def create_attempt(payload: AttemptIn):
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
async def mr_session_start(payload: MRSessionStartIn):
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
async def mr_session_end(payload: MRSessionEndIn):
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
async def mr_question(payload: MRQuestionIn):
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
