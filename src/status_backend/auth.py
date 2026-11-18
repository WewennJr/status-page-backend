"""Authentification : setup first-run, sessions par cookie."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Admin, SessionToken, utcnow

SESSION_COOKIE = "sp_session"
SESSION_TTL = timedelta(days=14)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# Vérifié quand l'utilisateur n'existe pas, pour éviter une fuite de timing.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_hex(16))


async def create_session(db: AsyncSession, admin_id: int) -> SessionToken:
    token = secrets.token_hex(32)
    session = SessionToken(
        token=token,
        admin_id=admin_id,
        created_at=utcnow(),
        expires_at=datetime.now(timezone.utc) + SESSION_TTL,
    )
    db.add(session)
    await db.commit()
    return session


async def get_current_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Admin:
    admin = await get_optional_admin(request, db)
    if admin is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return admin


async def get_optional_admin(request: Request, db: AsyncSession) -> Admin | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    result = await db.execute(select(SessionToken).where(SessionToken.token == token))
    session = result.scalar_one_or_none()
    if session is None or session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(
        timezone.utc
    ):
        return None
    return await db.get(Admin, session.admin_id)
