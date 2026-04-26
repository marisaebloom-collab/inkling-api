"""models.py — SQLAlchemy ORM models for Inkling.

Four tables:
  User          — account + library_built flag
  AuthorProfile — one row per known author, aggregated from reading history CSV
  ScanResult    — per-user scan history (replaces recents.json)
  UserSettings  — verdict thresholds + integration flags

Design principle: store only what the scoring algorithm needs as *input*.
Tags, individual book titles, ISBNs, shelves — all generated or fetched at
scan time. The CSV upload aggregates into AuthorProfile rows and is discarded.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


def _now():
    return datetime.now(timezone.utc)


# ── User ───────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = 'users'

    id              = Column(Integer, primary_key=True, index=True)
    email           = Column(String, unique=True, nullable=True, index=True)
    hashed_password = Column(String, nullable=True)   # nullable — third-party auth only for users
    apple_user_id   = Column(String, unique=True, nullable=True, index=True)
    google_user_id  = Column(String, unique=True, nullable=True, index=True)
    created_at      = Column(DateTime(timezone=True), default=_now)
    library_built   = Column(Boolean, default=False, nullable=False)

    authors  = relationship('AuthorProfile', back_populates='user', cascade='all, delete-orphan')
    books    = relationship('UserBook',      back_populates='user', cascade='all, delete-orphan')
    scans    = relationship('ScanResult',    back_populates='user', cascade='all, delete-orphan')
    settings = relationship('UserSettings',  back_populates='user', cascade='all, delete-orphan',
                            uselist=False)


# ── AuthorProfile ─────────────────────────────────────────────────────────────

class AuthorProfile(Base):
    """One row per author the user has read and rated.

    Aggregated at library-upload time from the Goodreads CSV.
    This is the only reading-history data stored — everything the scoring
    algorithm needs to compute author_signal, pred5, momentum, and div_bonus.

    Name stored in canonical 'First Last' form; the upload normalises
    'Last, First' CSV entries before inserting.
    """
    __tablename__ = 'author_profiles'
    __table_args__ = (
        UniqueConstraint('user_id', 'author_name', name='uq_author_per_user'),
    )

    id      = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'),
                     nullable=False, index=True)

    author_name          = Column(String, nullable=False, index=True)
    books_read           = Column(Integer, default=0, nullable=False)
    avg_rating           = Column(Float,   default=0.0, nullable=False)  # user's avg 0–5
    best_rating          = Column(Integer, default=0, nullable=False)    # highest single rating
    rate_4plus           = Column(Float,   default=0.0, nullable=False)  # fraction rated ≥4★
    rate_5star           = Column(Float,   default=0.0, nullable=False)  # fraction rated 5★
    most_recent_year_read = Column(Integer, nullable=True)               # for momentum calc

    created_at  = Column(DateTime(timezone=True), default=_now)
    updated_at  = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    user = relationship('User', back_populates='authors')


# ── UserBook ──────────────────────────────────────────────────────────────────

class UserBook(Base):
    """Raw per-book reading history, stored at CSV upload time.

    Held through calibration so Claude can analyze individual books.
    After calibration, rows remain for 'already read' detection at scan time
    and to allow re-calibration later.
    """
    __tablename__ = 'user_books'

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'),
                         nullable=False, index=True)

    title       = Column(String, nullable=False)
    author      = Column(String, nullable=False)    # normalized First Last
    user_rating = Column(Float,  nullable=True)     # 0–5; None if shelved unread
    date_read   = Column(Integer, nullable=True)    # year only, for momentum
    isbn        = Column(String,  nullable=True, index=True)
    shelf       = Column(String,  nullable=True)    # read | currently-reading | to-read
    tags        = Column(String,  nullable=True)    # JSON array; populated at calibration

    user = relationship('User', back_populates='books')


# ── ScanResult ────────────────────────────────────────────────────────────────

class ScanResult(Base):
    """Per-user scan history — replaces the flat recents.json file.

    Stores the output of each scoring call so the recents screen has
    persistent, per-user history. Also serves as a repeat-scan cache:
    if a user scans the same ISBN twice, we can return the stored result
    rather than calling Claude again.
    """
    __tablename__ = 'scan_results'

    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'),
                          nullable=False, index=True)

    isbn         = Column(String, nullable=True, index=True)
    title        = Column(String, nullable=False)
    author       = Column(String, nullable=False)
    cover_url    = Column(String, nullable=True)
    verdict      = Column(String, nullable=False)   # 'Strong Inkling' | 'On the Fence' | 'Hard Pass'
    match_pct    = Column(Integer, nullable=False)
    master_score = Column(Float,   nullable=False)
    scanned_at   = Column(DateTime(timezone=True), default=_now, index=True)

    # Snapshot of vibe/genre for filtering on the recents screen
    vibe_tags = Column(String, nullable=True)   # comma-separated e.g. "Dark,Plot-Driven"
    genre     = Column(String, nullable=True)   # e.g. "Fantasy"

    user = relationship('User', back_populates='scans')


# ── UserSettings ──────────────────────────────────────────────────────────────

class UserSettings(Base):
    """Per-user algorithm settings and integration flags.

    threshold_* values mirror weights.py THRESHOLDS defaults and must be
    threaded into score_book() when the per-user scoring path is active.
    """
    __tablename__ = 'user_settings'
    __table_args__ = (UniqueConstraint('user_id', name='uq_user_settings_user'),)

    id      = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'),
                     nullable=False, index=True)

    threshold_strong = Column(Float, default=0.90, nullable=False)
    threshold_keep   = Column(Float, default=0.75, nullable=False)
    threshold_maybe  = Column(Float, default=0.60, nullable=False)

    goodreads_connected  = Column(Boolean, default=False, nullable=False)
    storygraph_connected = Column(Boolean, default=False, nullable=False)

    # Per-user algorithm weights produced by calibration — JSON blob.
    # Keys: component_weights, reward_weights, risk_weights, taste_summary.
    # None = user hasn't calibrated yet; scoring falls back to global weights.py values.
    algorithm_weights = Column(String, nullable=True)

    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    user = relationship('User', back_populates='settings')
