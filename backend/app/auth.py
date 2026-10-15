"""Session-cookie authentication and role-based access dependencies."""

from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AuthSession, User, UserRole
from app.security import hash_session_token, new_session_token


async def create_session(db: AsyncSession, user: User, response: Response) -> None:
    token = new_session_token()
    expires_at = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    # Opportunistically drop this user's expired sessions so the table stays small.
    await db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id, AuthSession.expires_at <= datetime.now(UTC)
        )
    )
    db.add(
        AuthSession(user_id=user.id, token_hash=hash_session_token(token), expires_at=expires_at)
    )
    await db.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


async def destroy_session(db: AsyncSession, request: Request, response: Response) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await db.execute(
            delete(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
        )
        await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    result = await db.execute(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            AuthSession.token_hash == hash_session_token(token),
            AuthSession.expires_at > datetime.now(UTC),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
