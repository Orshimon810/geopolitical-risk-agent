"""
Authentication router — /auth/register, /auth/login, /auth/refresh,
/auth/logout, /auth/forgot-password, /auth/reset-password.

Registration hashes the password via bcrypt (handled inside dal.create_user).
Login issues a JWT access token + a long-lived refresh token (httpOnly cookie).
Refresh rotates the refresh token and issues a new access token.
Logout revokes the refresh token from Redis and clears the cookie.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from functools import partial

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.email import send_password_reset_email
from api.core.redis_client import get_redis
from api.core.security import create_access_token
from api.dependencies import db_session
from api.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
)
from georisk_agent.app.config import settings
from georisk_agent.db.dal import (
    authenticate_user,
    consume_reset_token,
    create_reset_token,
    create_user,
    get_user_by_email,
    get_user_by_id,
    get_valid_reset_token,
)

_REFRESH_COOKIE = "georisk_refresh"

router = APIRouter(prefix="/auth", tags=["Authentication"])

_REGISTER_LIMIT = 5    # max registrations per IP per hour
_REGISTER_TTL   = 3600
_LOGIN_LIMIT    = 10   # max login attempts per IP per 15 minutes
_LOGIN_TTL      = 900


async def _check_register_rate(request: Request, redis_client: aioredis.Redis) -> None:
    """Block more than 5 registration attempts per IP per hour."""
    client_ip = request.client.host if request.client else "unknown"
    hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"reg:{client_ip}:{hour_bucket}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, _REGISTER_TTL)
    if count > _REGISTER_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Please try again later.",
            headers={"Retry-After": "3600"},
        )


async def _check_login_rate(request: Request, redis_client: aioredis.Redis) -> None:
    """Block more than 10 login attempts per IP per 15 minutes."""
    client_ip = request.client.host if request.client else "unknown"
    window = datetime.now(timezone.utc).strftime("%Y%m%d%H") + str(datetime.now(timezone.utc).minute // 15)
    key = f"login:{client_ip}:{window}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, _LOGIN_TTL)
    if count > _LOGIN_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": "900"},
        )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
async def register(
    request: Request,
    body: RegisterRequest,
    session: AsyncSession = Depends(db_session),
    redis_client: aioredis.Redis = Depends(get_redis),
) -> UserResponse:
    await _check_register_rate(request, redis_client)
    existing = await get_user_by_email(session, body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists.",
        )

    try:
        user = await create_user(
            session,
            email=body.email,
            password_plaintext=body.password,
            full_name=body.full_name,
        )
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists.",
        )

    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        tier=user.tier,
        is_active=user.is_active,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for a JWT access token",
)
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    session: AsyncSession = Depends(db_session),
    redis_client: aioredis.Redis = Depends(get_redis),
) -> TokenResponse:
    await _check_login_rate(request, redis_client)
    user = await authenticate_user(session, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    await _set_refresh_cookie(response, redis_client, str(user.id))
    return TokenResponse(access_token=access_token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token (cookie) for a new access token",
)
async def refresh_token(
    request: Request,
    response: Response,
    redis_client: aioredis.Redis = Depends(get_redis),
    session: AsyncSession = Depends(db_session),
) -> TokenResponse:
    token = request.cookies.get(_REFRESH_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token present.",
        )

    user_id_bytes = await redis_client.get(f"refresh:{token}")
    if not user_id_bytes:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired or invalid.",
        )

    # Rotate — delete old token immediately to prevent reuse
    await redis_client.delete(f"refresh:{token}")

    user = await get_user_by_id(session, uuid.UUID(user_id_bytes.decode()))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account inactive.",
        )

    new_access_token = create_access_token({"sub": str(user.id), "email": user.email})
    await _set_refresh_cookie(response, redis_client, str(user.id))
    return TokenResponse(access_token=new_access_token)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Revoke the refresh token and clear the cookie",
)
async def logout(
    request: Request,
    response: Response,
    redis_client: aioredis.Redis = Depends(get_redis),
) -> MessageResponse:
    token = request.cookies.get(_REFRESH_COOKIE)
    if token:
        await redis_client.delete(f"refresh:{token}")
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out successfully.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _set_refresh_cookie(
    response: Response,
    redis_client: aioredis.Redis,
    user_id: str,
) -> None:
    """Generate a new refresh token, store in Redis, set as httpOnly cookie."""
    token = str(uuid.uuid4())
    ttl = settings.refresh_token_expire_days * 86_400
    await redis_client.setex(f"refresh:{token}", ttl, user_id)
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.is_prod,
        samesite="none" if settings.is_prod else "lax",
        max_age=ttl,
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=_REFRESH_COOKIE, path="/auth")


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Request a password reset email",
)
async def forgot_password(
    body: ForgotPasswordRequest,
    session: AsyncSession = Depends(db_session),
) -> MessageResponse:
    user = await get_user_by_email(session, body.email)
    if user is not None:
        raw_token = await create_reset_token(session, user.id)
        reset_link = f"{settings.frontend_url}/reset-password?token={raw_token}"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, partial(send_password_reset_email, user.email, reset_link)
            )
        except Exception:
            pass  # log internally; never surface email errors to the caller
    # Always return the same message to prevent email enumeration.
    return MessageResponse(
        message="If an account exists for this email, a reset link has been sent."
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password using a reset token",
)
async def reset_password(
    body: ResetPasswordRequest,
    session: AsyncSession = Depends(db_session),
) -> MessageResponse:
    token_record = await get_valid_reset_token(session, body.token)
    if token_record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid or has expired.",
        )
    await consume_reset_token(session, token_record, body.new_password)
    return MessageResponse(message="Password updated successfully. You can now log in.")
