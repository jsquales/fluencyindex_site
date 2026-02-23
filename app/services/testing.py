from datetime import datetime

from sqlalchemy import text

from ..db import Session, SessionLocal


class CheckinError(Exception):
    pass


def is_valid_id(student_id: str) -> bool:
    sid = (student_id or "").strip()
    return len(sid) == 6 and sid.isdigit()


def start_session(student_id: str) -> int:
    """Validate student, create a new session, return session id."""
    sid = (student_id or "").strip()
    if not is_valid_id(sid):
        raise CheckinError("Student ID must be exactly 6 digits.")

    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT s.class_id, c.teacher_id
                FROM students s
                JOIN classes c ON c.id = s.class_id
                WHERE s.student_identifier = :student_id
                  AND s.is_active = :is_active
                LIMIT 1
                """
            ),
            {"student_id": sid, "is_active": True},
        ).first()

        if not row:
            raise CheckinError("Student ID not found (or inactive).")

        session = Session(
            class_id=int(row.class_id),
            teacher_id=int(row.teacher_id),
            status="scheduled",
            started_at=datetime.utcnow(),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return int(session.id)
    except CheckinError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
