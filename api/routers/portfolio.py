"""
Portfolio router — CRUD for user investment holdings.

GET  /portfolio/holdings          → list all holdings for the authenticated user
POST /portfolio/holdings          → add a holding (max 20 per user)
PUT  /portfolio/holdings/{id}     → update name / quantity / value_usd
DELETE /portfolio/holdings/{id}   → remove a holding
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import db_session, get_current_user
from api.schemas.portfolio import (
    PortfolioHoldingCreate,
    PortfolioHoldingResponse,
    PortfolioHoldingUpdate,
)
from georisk_agent.db.dal import (
    add_holding,
    count_holdings,
    delete_holding,
    get_user_portfolio,
    update_holding,
)
from georisk_agent.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

_MAX_HOLDINGS = 20


@router.get(
    "/holdings",
    response_model=list[PortfolioHoldingResponse],
    summary="List all holdings for the authenticated user",
)
async def list_holdings(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> list[PortfolioHoldingResponse]:
    holdings = await get_user_portfolio(session, current_user.id)
    return [PortfolioHoldingResponse.from_orm_model(h) for h in holdings]


@router.post(
    "/holdings",
    response_model=PortfolioHoldingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a holding to the user's portfolio (max 20)",
)
async def create_holding(
    body: PortfolioHoldingCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> PortfolioHoldingResponse:
    count = await count_holdings(session, current_user.id)
    if count >= _MAX_HOLDINGS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Portfolio limit reached ({_MAX_HOLDINGS} holdings maximum). Remove a holding before adding a new one.",
        )

    try:
        holding = await add_holding(
            session,
            user_id=current_user.id,
            ticker=body.ticker,
            name=body.name,
            asset_type=body.asset_type,
            quantity=body.quantity,
            value_usd=body.value_usd,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ticker {body.ticker!r} is already in your portfolio.",
        )

    logger.info("Added holding ticker=%r user=%s", body.ticker, current_user.id)
    return PortfolioHoldingResponse.from_orm_model(holding)


@router.put(
    "/holdings/{holding_id}",
    response_model=PortfolioHoldingResponse,
    summary="Update name, quantity, or value_usd of a holding",
)
async def update_holding_endpoint(
    holding_id: str,
    body: PortfolioHoldingUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> PortfolioHoldingResponse:
    try:
        hid = uuid.UUID(holding_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid holding ID.")

    # Use sentinel to distinguish "not provided" from "set to None"
    quantity = body.quantity if body.quantity is not None else ...
    value_usd = body.value_usd if body.value_usd is not None else ...

    holding = await update_holding(
        session,
        hid,
        current_user.id,
        name=body.name,
        quantity=quantity,  # type: ignore[arg-type]
        value_usd=value_usd,  # type: ignore[arg-type]
    )
    if holding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found.")

    await session.commit()
    return PortfolioHoldingResponse.from_orm_model(holding)


@router.delete(
    "/holdings/{holding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a holding from the user's portfolio",
)
async def delete_holding_endpoint(
    holding_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(db_session),
) -> None:
    try:
        hid = uuid.UUID(holding_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid holding ID.")

    deleted = await delete_holding(session, hid, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found.")

    await session.commit()
    logger.info("Deleted holding id=%s user=%s", holding_id, current_user.id)
