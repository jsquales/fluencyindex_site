from fractions import Fraction
from types import SimpleNamespace

import pytest

from app.schemas.game24 import Game24AttemptRowIn
from app.services.game24_parser import parse_number
from app.services.game24_validator import validate_game24_submission


def puzzle(*numbers: str):
    return SimpleNamespace(
        n1_raw=numbers[0],
        n2_raw=numbers[1],
        n3_raw=numbers[2],
        n4_raw=numbers[3],
    )


def row(left: str, op: str, right: str, result: str) -> Game24AttemptRowIn:
    return Game24AttemptRowIn(left=left, op=op, right=right, result=result)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", Fraction(3)),
        ("-8", Fraction(-8)),
        (".9", Fraction(9, 10)),
        ("0.9", Fraction(9, 10)),
        ("-1.25", Fraction(-5, 4)),
        ("1/2", Fraction(1, 2)),
        ("2/4", Fraction(1, 2)),
    ],
)
def test_parse_number_accepts_allowed_formats(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "1 1/2", "3+4", "1/", "/2", "1/0", "1.", "1.2.3", " 3", "3 "])
def test_parse_number_rejects_invalid_formats(raw):
    with pytest.raises(ValueError):
        parse_number(raw)


def test_validate_game24_accepts_valid_chain_with_recreated_value():
    result = validate_game24_submission(
        puzzle=puzzle("1", "2", "3", "4"),
        rows=[
            row("3", "*", "4", "12"),
            row("2", "*", "12", "24"),
            row("24", "*", "1", "24"),
        ],
    )

    assert result.is_valid is True


def test_validate_game24_rejects_reusing_consumed_value():
    result = validate_game24_submission(
        puzzle=puzzle("3", "6", "4", "2"),
        rows=[
            row("3", "*", "4", "12"),
            row("3", "*", "2", "6"),
            row("12", "+", "6", "18"),
        ],
    )

    assert result.is_valid is False
    assert result.error_code == "INPUT_NOT_AVAILABLE"
    assert result.row_number == 2


def test_validate_game24_counts_duplicate_values():
    result = validate_game24_submission(
        puzzle=puzzle("6", "6", "4", "2"),
        rows=[
            row("6", "*", "6", "36"),
            row("4", "*", "2", "8"),
            row("36", "-", "8", "28"),
        ],
    )

    assert result.error_code == "FINAL_RESULT_NOT_24"


def test_validate_game24_rejects_insufficient_duplicate_count():
    result = validate_game24_submission(
        puzzle=puzzle("6", "3", "4", "2"),
        rows=[
            row("6", "*", "6", "36"),
            row("4", "*", "2", "8"),
            row("36", "-", "8", "28"),
        ],
    )

    assert result.is_valid is False
    assert result.error_code == "INSUFFICIENT_DUPLICATE_COUNT"


def test_validate_game24_rejects_divide_by_zero():
    result = validate_game24_submission(
        puzzle=puzzle("1", "0", "3", "4"),
        rows=[
            row("1", "/", "0", "0"),
            row("3", "*", "4", "12"),
            row("12", "+", "12", "24"),
        ],
    )

    assert result.error_code == "DIVIDE_BY_ZERO"
