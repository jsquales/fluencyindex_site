from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func

from ..models.game24 import Game24Attempt, Game24AttemptRow, Game24Puzzle
from ..schemas.game24 import Game24AttemptRowIn


def get_puzzle_by_id(db: Session, puzzle_id: int) -> Game24Puzzle | None:
    return (
        db.query(Game24Puzzle)
        .filter(Game24Puzzle.id == puzzle_id, Game24Puzzle.is_active.is_(True))
        .first()
    )


def get_random_active_puzzle(
    db: Session,
    variant: str | None = None,
    difficulty: str | None = None,
) -> Game24Puzzle | None:
    query = db.query(Game24Puzzle).filter(Game24Puzzle.is_active.is_(True))
    if variant:
        query = query.filter(Game24Puzzle.variant == variant)
    if difficulty:
        query = query.filter(Game24Puzzle.difficulty == difficulty)
    return query.order_by(func.random()).first()


def get_active_puzzle_difficulties(db: Session) -> list[str]:
    rows = (
        db.query(Game24Puzzle.difficulty)
        .filter(Game24Puzzle.is_active.is_(True))
        .distinct()
        .all()
    )
    difficulties = [str(row[0]) for row in rows if row[0] is not None]
    return sorted(difficulties, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))


def create_attempt(
    db: Session,
    *,
    puzzle: Game24Puzzle,
    student_identifier: str | None,
    started_at,
    submitted_at,
    response_time_ms: int,
    is_correct: bool,
    error_code: str | None,
    error_message: str | None,
) -> Game24Attempt:
    attempt = Game24Attempt(
        puzzle_id=puzzle.id,
        student_identifier=student_identifier,
        started_at=started_at,
        submitted_at=submitted_at,
        response_time_ms=response_time_ms,
        is_correct=is_correct,
        error_code=error_code,
        error_message=error_message,
        variant=puzzle.variant,
        difficulty=puzzle.difficulty,
    )
    db.add(attempt)
    db.flush()
    return attempt


def create_attempt_rows(
    db: Session,
    *,
    attempt_id: int,
    rows: list[Game24AttemptRowIn],
) -> list[Game24AttemptRow]:
    created_rows = []
    for index, row in enumerate(rows, start=1):
        attempt_row = Game24AttemptRow(
            attempt_id=attempt_id,
            row_number=index,
            left_raw=row.left,
            operator=row.op,
            right_raw=row.right,
            result_raw=row.result,
        )
        db.add(attempt_row)
        created_rows.append(attempt_row)
    db.flush()
    return created_rows
