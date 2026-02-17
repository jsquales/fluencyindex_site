from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text

from .db import SessionLocal, WaitlistEntry, init_db  # NEW

BASE_DIR = Path(__file__).resolve().parent  # app/

app = FastAPI()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
