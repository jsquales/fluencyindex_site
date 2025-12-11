import os
from datetime import datetime

from sqlalchemy import (
    create_engine,
    Integer,
    String,
    Text,
    DateTime,
    func,
)
from sqlalchemy.orm import (
    declarative_base,
    sessionmaker,
    Mapped,
    mapped_column,
)

# Get the DATABASE_URL from environment variables (Render, local .env, etc.)
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Fallback for local development: use a SQLite file
    print("DATABASE_URL not set; using local SQLite database 'dev.db'")
    DATABASE_URL = "sqlite:///dev.db"
else:
    # On Render, ensure we use the psycopg (v3) driver instead of default psycopg2
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


# Create the SQLAlchemy engine and session factory
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

Base = declarative_base()


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


def init_db() -> None:
    """Create tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)
