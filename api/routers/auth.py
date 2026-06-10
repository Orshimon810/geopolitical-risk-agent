"""
Authentication router — /auth/register and /auth/login.

Registration hashes the password via bcrypt (handled inside dal.create_user).
Login issues a JWT access token on valid credentials.
"""

from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.redis_client import get_redis
from api.core.security import create_access_token
from api.dependencies import db_session
from api.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from georisk_agent.db.dal import authenticate_user, create_user, get_user_by_email

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
    # Pre-check to give a clean error instead of relying on the DB constraint alone.
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
        # Race-condition safety net: another request registered the same email
        # between our SELECT and INSERT.
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
        # Use the same generic message for missing user and wrong password to
        # prevent user-enumeration via distinct error messages.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)
