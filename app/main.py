from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent  # app/

app = FastAPI()

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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

    For now, we just log it; later we can store in a DB or send an email.
    """
    # This will show up in Render logs:
    print("New waitlist signup:", {"name": name, "role": role, "email": email, "notes": notes})

    return templates.TemplateResponse(
        "signup_thanks.html",
        {
            "request": request,
            "page_title": "Thanks for Joining the Waitlist | Fluency Index",
            "name": name,
        }
    )
