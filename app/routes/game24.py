from fastapi import APIRouter, HTTPException, Query

from ..db import SessionLocal
from ..schemas.game24 import (
    Game24OptionsResponse,
    Game24PuzzleResponse,
    Game24SubmitRequest,
    Game24SubmitResponse,
)
from ..services.game24_service import (
    get_game24_options_response,
    get_game24_puzzle_response,
    get_random_game24_puzzle_response,
    submit_game24_attempt,
)

router = APIRouter(prefix="/api/v1/game24", tags=["game24"])


@router.post("/submit", response_model=Game24SubmitResponse)
async def submit_game24(payload: Game24SubmitRequest) -> Game24SubmitResponse:
    db = SessionLocal()
    try:
        return submit_game24_attempt(db, payload)
    finally:
        db.close()


@router.get("/options", response_model=Game24OptionsResponse)
async def get_game24_options() -> Game24OptionsResponse:
    db = SessionLocal()
    try:
        return get_game24_options_response(db)
    finally:
        db.close()


@router.get("/random-puzzle", response_model=Game24PuzzleResponse)
async def get_random_game24_puzzle_alias(
    variant: str | None = Query(default=None, max_length=64),
    difficulty: str | None = Query(default=None, max_length=64),
) -> Game24PuzzleResponse:
    return _get_random_game24_puzzle(variant=variant, difficulty=difficulty)


@router.get("/puzzle/random", response_model=Game24PuzzleResponse)
async def get_random_game24_puzzle(
    variant: str | None = Query(default=None, max_length=64),
    difficulty: str | None = Query(default=None, max_length=64),
) -> Game24PuzzleResponse:
    return _get_random_game24_puzzle(variant=variant, difficulty=difficulty)


def _get_random_game24_puzzle(variant: str | None, difficulty: str | None) -> Game24PuzzleResponse:
    db = SessionLocal()
    try:
        puzzle = get_random_game24_puzzle_response(db, variant=variant, difficulty=difficulty)
        if not puzzle:
            raise HTTPException(status_code=404, detail="Puzzle not found.")
        return puzzle
    finally:
        db.close()


@router.get("/puzzle/{puzzle_id}", response_model=Game24PuzzleResponse)
async def get_game24_puzzle(puzzle_id: int) -> Game24PuzzleResponse:
    db = SessionLocal()
    try:
        puzzle = get_game24_puzzle_response(db, puzzle_id)
        if not puzzle:
            raise HTTPException(status_code=404, detail="Puzzle not found.")
        return puzzle
    finally:
        db.close()
