from sqlalchemy.orm import Session

from ..crud.game24 import (
    create_attempt,
    create_attempt_rows,
    get_active_puzzle_difficulties,
    get_puzzle_by_id,
    get_random_active_puzzle,
)
from ..schemas.game24 import (
    Game24OptionsResponse,
    Game24PuzzleResponse,
    Game24SubmitRequest,
    Game24SubmitResponse,
    Game24VariantOption,
)
from .game24_validator import Game24ValidationResult, validate_game24_submission


def compute_response_time_ms(payload: Game24SubmitRequest) -> int:
    delta = payload.submitted_at - payload.started_at
    return max(0, int(delta.total_seconds() * 1000))


def get_game24_puzzle_response(db: Session, puzzle_id: int) -> Game24PuzzleResponse | None:
    puzzle = get_puzzle_by_id(db, puzzle_id)
    if not puzzle:
        return None
    return puzzle_to_response(puzzle)


VARIANT_OPTIONS = [
    Game24VariantOption(value="single_digits", label="Single Digits"),
    Game24VariantOption(value="integers", label="Integers"),
]


def get_random_game24_puzzle_response(
    db: Session,
    variant: str | None = None,
    difficulty: str | None = None,
) -> Game24PuzzleResponse | None:
    puzzle = get_random_active_puzzle(db, variant=variant, difficulty=difficulty)
    if not puzzle:
        return None
    return puzzle_to_response(puzzle)


def get_game24_options_response(db: Session) -> Game24OptionsResponse:
    return Game24OptionsResponse(
        variants=VARIANT_OPTIONS,
        difficulties=get_active_puzzle_difficulties(db),
    )


def puzzle_to_response(puzzle) -> Game24PuzzleResponse:
    return Game24PuzzleResponse(
        id=puzzle.id,
        variant=puzzle.variant,
        difficulty=puzzle.difficulty,
        style=puzzle.style,
        numbers=[puzzle.n1_raw, puzzle.n2_raw, puzzle.n3_raw, puzzle.n4_raw],
    )


def submit_game24_attempt(db: Session, payload: Game24SubmitRequest) -> Game24SubmitResponse:
    puzzle = get_puzzle_by_id(db, payload.puzzle_id)
    response_time_ms = compute_response_time_ms(payload)

    if puzzle:
        validation = validate_game24_submission(puzzle=puzzle, rows=payload.rows)
    else:
        validation = Game24ValidationResult(
            is_valid=False,
            error_code="PUZZLE_NOT_FOUND",
            error_message="Puzzle not found.",
        )

    try:
        if puzzle:
            attempt = create_attempt(
                db,
                puzzle=puzzle,
                student_identifier=payload.student_identifier,
                started_at=payload.started_at,
                submitted_at=payload.submitted_at,
                response_time_ms=response_time_ms,
                is_correct=validation.is_valid,
                error_code=validation.error_code,
                error_message=validation.error_message,
            )
            if len(payload.rows) <= 3:
                create_attempt_rows(db, attempt_id=attempt.id, rows=payload.rows)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return Game24SubmitResponse(
        is_correct=validation.is_valid,
        response_time_ms=response_time_ms,
        error_code=validation.error_code,
        error_message=validation.error_message,
        row_number=validation.row_number,
    )
