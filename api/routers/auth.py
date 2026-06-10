"""
Authentication router — /auth/register, /auth/login, /auth/forgot-password,
/auth/reset-password.

Registration hashes the password via bcrypt (handled inside dal.create_user).
Login issues a JWT access token on valid credentials.
Forgot/reset-password implement a one-time, time-limited token flow.
"""

from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    get_valid_reset_token,
)

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

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


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
            send_password_reset_email(user.email, reset_link)
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
