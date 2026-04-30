from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Game24AttemptRowIn(BaseModel):
    left: str = Field(max_length=64)
    op: str = Field(max_length=64)
    right: str = Field(max_length=64)
    result: str = Field(max_length=64)


class Game24SubmitRequest(BaseModel):
    puzzle_id: int = Field(ge=1, le=2_147_483_647)
    student_identifier: Optional[str] = Field(default=None, max_length=128)
    started_at: datetime
    submitted_at: datetime
    rows: list[Game24AttemptRowIn]


class Game24SubmitResponse(BaseModel):
    is_correct: bool
    response_time_ms: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    row_number: Optional[int] = None


class Game24PuzzleResponse(BaseModel):
    id: int
    variant: str
    difficulty: str
    style: Optional[str] = None
    numbers: list[str]


class Game24VariantOption(BaseModel):
    value: str
    label: str


class Game24OptionsResponse(BaseModel):
    variants: list[Game24VariantOption]
    difficulties: list[str]
