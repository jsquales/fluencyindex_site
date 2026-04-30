from datetime import datetime
from typing import List

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class Game24Puzzle(Base):
    __tablename__ = "game24_puzzles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    variant: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(64), nullable=False)
    style: Mapped[str | None] = mapped_column(String(64), nullable=True)
    n1_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    n2_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    n3_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    n4_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sheet: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    attempts: Mapped[List["Game24Attempt"]] = relationship(back_populates="puzzle")


class Game24Attempt(Base):
    __tablename__ = "game24_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    puzzle_id: Mapped[int] = mapped_column(
        ForeignKey("game24_puzzles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    student_identifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    variant: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    puzzle: Mapped["Game24Puzzle"] = relationship(back_populates="attempts")
    rows: Mapped[List["Game24AttemptRow"]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class Game24AttemptRow(Base):
    __tablename__ = "game24_attempt_rows"
    __table_args__ = (
        CheckConstraint("row_number IN (1, 2, 3)", name="game24_attempt_row_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("game24_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    left_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    operator: Mapped[str] = mapped_column(String(64), nullable=False)
    right_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    result_raw: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    attempt: Mapped["Game24Attempt"] = relationship(back_populates="rows")
