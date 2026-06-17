"""
Portfolio router — CRUD for user investment holdings.

GET  /portfolio/holdings          → list all holdings for the authenticated user
POST /portfolio/holdings          → add a holding (max 20 per user)
PUT  /portfolio/holdings/{id}     → update name / quantity / cost_basis_usd
DELETE /portfolio/holdings/{id}   → remove a holding
GET  /portfolio/search?q=         → ticker/company autocomplete (via yfinance)
GET  /portfolio/quote?ticker=     → live price for a single ticker (via yfinance)
GET  /portfolio/quotes?tickers=   → live prices for multiple tickers, comma-separated
"""

import asyncio
import logging
import uuid

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import db_session, get_current_user
from api.schemas.portfolio import (
    PortfolioHoldingCreate,
    PortfolioHoldingResponse,
    PortfolioHoldingUpdate,
    TickerQuoteResponse,
    TickerSearchResult,
)
from georisk_agent.db.dal import (
    add_holding,
    count_holdings,
    delete_holding,
    get_user_portfolio,
    update_holding,
)
from georisk_agent.db.models import User

_QUOTE_TYPE_TO_ASSET: dict[str, str] = {
    "EQUITY": "stock",
    "ETF": "etf",
    "MUTUALFUND": "etf",
    "CRYPTOCURRENCY": "crypto",
    "FUTURE": "commodity",
    "COMMODITY": "commodity",
    "BOND": "bond",
    "FIXED_INCOME": "bond",
    "INDEX": "etf",
}

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
            cost_basis_usd=body.cost_basis_usd,
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
    summary="Update name, quantity, or cost_basis_usd of a holding",
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
    cost_basis_usd = body.cost_basis_usd if body.cost_basis_usd is not None else ...

    holding = await update_holding(
        session,
        hid,
        current_user.id,
        name=body.name,
        quantity=quantity,  # type: ignore[arg-type]
        cost_basis_usd=cost_basis_usd,  # type: ignore[arg-type]
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


@router.get(
    "/search",
    response_model=list[TickerSearchResult],
    summary="Autocomplete ticker/company search (max 8 results)",
)
async def search_tickers(
    q: str = Query(min_length=1, max_length=50, description="Ticker symbol or company name"),
    current_user: User = Depends(get_current_user),
) -> list[TickerSearchResult]:
    def _search() -> list[TickerSearchResult]:
        try:
            results = yf.Search(q, max_results=8, news_count=0)
            out: list[TickerSearchResult] = []
            for quote in results.quotes:
                symbol: str = quote.get("symbol", "")
                if not symbol:
                    continue
                name: str = quote.get("longname") or quote.get("shortname") or symbol
                qt: str = (quote.get("quoteType") or "").upper()
                asset_type = _QUOTE_TYPE_TO_ASSET.get(qt, "stock")
                out.append(TickerSearchResult(ticker=symbol, name=name, asset_type=asset_type))
            return out
        except Exception:
            return []

    return await asyncio.to_thread(_search)


@router.get(
    "/quote",
    response_model=TickerQuoteResponse,
    summary="Fetch live price for a single ticker",
)
async def get_ticker_quote(
    ticker: str = Query(min_length=1, max_length=20, description="Yahoo Finance ticker symbol"),
    current_user: User = Depends(get_current_user),
) -> TickerQuoteResponse:
    def _quote() -> TickerQuoteResponse:
        try:
            info = yf.Ticker(ticker.upper()).fast_info
            price = float(info.last_price) if info.last_price is not None else None
            currency: str = getattr(info, "currency", None) or "USD"
            return TickerQuoteResponse(ticker=ticker.upper(), price=price, currency=currency)
        except Exception:
            return TickerQuoteResponse(ticker=ticker.upper(), price=None, currency="USD")

    return await asyncio.to_thread(_quote)


@router.get(
    "/quotes",
    response_model=list[TickerQuoteResponse],
    summary="Fetch live prices for multiple tickers in one call (max 20, comma-separated)",
)
async def get_ticker_quotes(
    tickers: str = Query(
        min_length=1,
        max_length=500,
        description="Comma-separated Yahoo Finance ticker symbols (e.g. AAPL,MSFT,BTC-USD)",
    ),
    current_user: User = Depends(get_current_user),
) -> list[TickerQuoteResponse]:
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()][:_MAX_HOLDINGS]

    def _batch_quote() -> list[TickerQuoteResponse]:
        results: list[TickerQuoteResponse] = []
        for t in ticker_list:
            try:
                info = yf.Ticker(t).fast_info
                price = float(info.last_price) if info.last_price is not None else None
                currency: str = getattr(info, "currency", None) or "USD"
                results.append(TickerQuoteResponse(ticker=t, price=price, currency=currency))
            except Exception:
                results.append(TickerQuoteResponse(ticker=t, price=None, currency="USD"))
        return results

    return await asyncio.to_thread(_batch_quote)
