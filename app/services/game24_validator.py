from dataclasses import dataclass
from fractions import Fraction

from ..models.game24 import Game24Puzzle
from ..schemas.game24 import Game24AttemptRowIn
from .game24_parser import parse_number
from .game24_pool import Game24Pool

VALID_OPERATORS = {"+", "-", "*", "/"}
TARGET_VALUE = Fraction(24)


@dataclass(frozen=True)
class Game24ValidationResult:
    is_valid: bool
    error_code: str | None = None
    error_message: str | None = None
    row_number: int | None = None


def _error(code: str, message: str, row_number: int | None = None) -> Game24ValidationResult:
    return Game24ValidationResult(
        is_valid=False,
        error_code=code,
        error_message=message,
        row_number=row_number,
    )


def _compute(left: Fraction, operator: str, right: Fraction) -> Fraction | None:
    if operator == "+":
        return left + right
    if operator == "-":
        return left - right
    if operator == "*":
        return left * right
    if operator == "/" and right != 0:
        return left / right
    return None


def validate_game24_submission(
    *,
    puzzle: Game24Puzzle,
    rows: list[Game24AttemptRowIn],
) -> Game24ValidationResult:
    if len(rows) != 3:
        return _error("INVALID_ROW_COUNT", "A submission must contain exactly 3 rows.")

    try:
        pool = Game24Pool(
            [
                parse_number(puzzle.n1_raw),
                parse_number(puzzle.n2_raw),
                parse_number(puzzle.n3_raw),
                parse_number(puzzle.n4_raw),
            ]
        )
    except ValueError:
        return _error("INVALID_NUMBER_FORMAT", "Puzzle contains an invalid card number.")

    final_result: Fraction | None = None
    for row_number, row in enumerate(rows, start=1):
        if row.op not in VALID_OPERATORS:
            return _error("INVALID_OPERATOR", "Operator must be one of +, -, *, /.", row_number)

        try:
            left = parse_number(row.left)
            right = parse_number(row.right)
            entered_result = parse_number(row.result)
        except ValueError:
            return _error("INVALID_NUMBER_FORMAT", "Number inputs must use a valid numeric format.", row_number)

        consume_error = pool.consume_pair(left, right)
        if consume_error:
            message = (
                "That value is not currently available."
                if consume_error == "INPUT_NOT_AVAILABLE"
                else "That duplicate value is not available enough times."
            )
            return _error(consume_error, message, row_number)

        computed_result = _compute(left, row.op, right)
        if computed_result is None:
            return _error("DIVIDE_BY_ZERO", "Division by zero is not allowed.", row_number)

        if computed_result != entered_result:
            return _error("INCORRECT_ROW_RESULT", "The row result does not match the operation.", row_number)

        pool.add(computed_result)
        final_result = computed_result

    if final_result != TARGET_VALUE:
        return _error("FINAL_RESULT_NOT_24", "The final row result must be exactly 24.", 3)

    remaining = pool.values()
    if len(remaining) != 1 or remaining[0] != TARGET_VALUE:
        return _error("INVALID_END_STATE", "The final available value must be exactly 24.", 3)

    return Game24ValidationResult(is_valid=True)
