"""
Authentication router — /auth/register and /auth/login.

Registration hashes the password via bcrypt (handled inside dal.create_user).
Login issues a JWT access token on valid credentials.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.security import create_access_token
from api.dependencies import db_session
from api.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from georisk_agent.db.dal import authenticate_user, create_user, get_user_by_email

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(db_session),
) -> UserResponse:
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
    body: LoginRequest,
    session: AsyncSession = Depends(db_session),
) -> TokenResponse:
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
