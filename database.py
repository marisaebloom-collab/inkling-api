"""database.py — SQLAlchemy engine, session, and get_db() dependency."""
from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Railway supplies DATABASE_URL with the legacy postgres:// scheme;
# SQLAlchemy 1.4+ requires postgresql://
_raw_url = os.environ.get('DATABASE_URL', '')
if _raw_url.startswith('postgres://'):
    _raw_url = _raw_url.replace('postgres://', 'postgresql://', 1)

DATABASE_URL = _raw_url or 'sqlite:///./inkling_dev.db'

engine = create_engine(
    DATABASE_URL,
    # Prevent stale connections after Railway drops idle Postgres connections
    pool_pre_ping=True,
    pool_recycle=300,
    # sqlite doesn't support these pool kwargs — guard them
    **({} if DATABASE_URL.startswith('sqlite') else {}),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session and ensures it's closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
