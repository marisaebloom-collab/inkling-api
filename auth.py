"""auth.py — JWT authentication router for Inkling.

Public auth surface: Apple Sign In, Google Sign In (third-party only).
No user-facing password creation or management.

Dev-only bypass: POST /auth/dev-login (active only when DEV_MODE=true env var is set).
Used during development via the hidden tap-logo gesture in the app UI.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets as _secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserSettings

# ── Config ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY:
    raise RuntimeError('SECRET_KEY environment variable is not set')

DEV_MODE   = os.environ.get('DEV_MODE', '').lower() == 'true'
ALGORITHM  = 'HS256'
EXPIRY_DAYS = 30

bearer = HTTPBearer()
router = APIRouter(prefix='/auth', tags=['auth'])


# ── Password helpers (dev bypass only — not exposed to users) ─────────────────

_PBKDF2_ITERS = 260_000


def _hash_password(password: str) -> str:
    salt = _secrets.token_bytes(16)
    key  = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, _PBKDF2_ITERS)
    return base64.b64encode(salt + key).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    raw             = base64.b64decode(hashed.encode())
    salt, stored    = raw[:16], raw[16:]
    new_key         = hashlib.pbkdf2_hmac('sha256', plain.encode(), salt, _PBKDF2_ITERS)
    return _secrets.compare_digest(stored, new_key)


# ── JWT ───────────────────────────────────────────────────────────────────────

def _create_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=EXPIRY_DAYS)
    return jwt.encode({'sub': str(user_id), 'exp': expire}, SECRET_KEY, algorithm=ALGORITHM)


# ── Default settings ──────────────────────────────────────────────────────────

def _create_default_settings(db: Session, user: User) -> None:
    db.add(UserSettings(user_id=user.id))
    db.commit()


# ── Dependency: get_current_user ──────────────────────────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """Validate JWT and return the authenticated User row."""
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Invalid or expired token',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        payload  = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id  = int(payload.get('sub', 0))
    except (JWTError, ValueError):
        raise exc

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise exc
    return user


# ── Request / Response schemas ────────────────────────────────────────────────

class AppleRequest(BaseModel):
    apple_user_id: str
    email:         str | None = None   # Apple only sends this on the user's first login


class GoogleRequest(BaseModel):
    google_user_id: str
    email:          str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = 'bearer'


class UserResponse(BaseModel):
    id:             int
    email:          str | None
    apple_user_id:  str | None
    google_user_id: str | None
    library_built:  bool
    created_at:     datetime

    class Config:
        from_attributes = True


# Dev-only schemas — not reachable unless DEV_MODE=true
class _DevLoginRequest(BaseModel):
    email:    EmailStr
    password: str


class _DevRegisterRequest(BaseModel):
    email:    EmailStr
    password: str


# ── Routes: Apple ─────────────────────────────────────────────────────────────

@router.post('/apple', response_model=TokenResponse)
def apple_auth(body: AppleRequest, db: Session = Depends(get_db)):
    """Sign in with Apple.

    Apple only sends email on first login; all subsequent calls have only apple_user_id.

    1. Known apple_user_id → return token
    2. Email matches existing account → link apple_user_id, return token
    3. New user → create account
    """
    user = db.query(User).filter(User.apple_user_id == body.apple_user_id).first()
    if user:
        return TokenResponse(access_token=_create_token(user.id))

    if body.email:
        user = db.query(User).filter(User.email == body.email).first()
        if user:
            user.apple_user_id = body.apple_user_id
            db.commit()
            return TokenResponse(access_token=_create_token(user.id))

    user = User(apple_user_id=body.apple_user_id, email=body.email or None)
    db.add(user)
    db.commit()
    db.refresh(user)
    _create_default_settings(db, user)
    return TokenResponse(access_token=_create_token(user.id))


# ── Routes: Google ────────────────────────────────────────────────────────────

@router.post('/google', response_model=TokenResponse)
def google_auth(body: GoogleRequest, db: Session = Depends(get_db)):
    """Sign in with Google.

    Requires ASWebAuthenticationSession on iOS (WKWebView blocks Google OAuth directly).
    Backend logic mirrors Apple: look up by google_user_id, link by email, or create.
    """
    user = db.query(User).filter(User.google_user_id == body.google_user_id).first()
    if user:
        return TokenResponse(access_token=_create_token(user.id))

    if body.email:
        user = db.query(User).filter(User.email == body.email).first()
        if user:
            user.google_user_id = body.google_user_id
            db.commit()
            return TokenResponse(access_token=_create_token(user.id))

    user = User(google_user_id=body.google_user_id, email=body.email or None)
    db.add(user)
    db.commit()
    db.refresh(user)
    _create_default_settings(db, user)
    return TokenResponse(access_token=_create_token(user.id))


# ── Routes: /me ───────────────────────────────────────────────────────────────

@router.get('/me', response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


# ── Routes: Dev bypass (DEV_MODE=true only) ───────────────────────────────────

@router.post('/dev-login', response_model=TokenResponse, include_in_schema=DEV_MODE)
def dev_login(body: _DevLoginRequest, db: Session = Depends(get_db)):
    """Dev-only email/password login. Hidden from API docs in production.
    Activated via the triple-tap logo gesture in the app UI.
    """
    if not DEV_MODE:
        raise HTTPException(404, 'Not found')

    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.hashed_password:
        raise HTTPException(401, 'Invalid credentials')
    if not _verify_password(body.password, user.hashed_password):
        raise HTTPException(401, 'Invalid credentials')
    return TokenResponse(access_token=_create_token(user.id))


@router.post('/dev-register', response_model=TokenResponse, status_code=201,
             include_in_schema=DEV_MODE)
def dev_register(body: _DevRegisterRequest, db: Session = Depends(get_db)):
    """Dev-only account creation with email + password. Hidden in production."""
    if not DEV_MODE:
        raise HTTPException(404, 'Not found')

    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, 'Email already registered')

    user = User(email=body.email, hashed_password=_hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    _create_default_settings(db, user)
    return TokenResponse(access_token=_create_token(user.id))


_MARISA_EMAIL = 'marisa@inkling.app'

@router.get('/dev-marisa-token', response_model=TokenResponse, include_in_schema=DEV_MODE)
def dev_marisa_token(db: Session = Depends(get_db)):
    """Dev-only: return a JWT for Marisa's account, creating it if it doesn't exist.
    No credentials required — hardcoded bypass for the 'Use as Marisa' button.
    """
    if not DEV_MODE:
        raise HTTPException(404, 'Not found')

    user = db.query(User).filter(User.email == _MARISA_EMAIL).first()
    if not user:
        user = User(email=_MARISA_EMAIL)
        db.add(user)
        db.commit()
        db.refresh(user)
        _create_default_settings(db, user)

    return TokenResponse(access_token=_create_token(user.id))
