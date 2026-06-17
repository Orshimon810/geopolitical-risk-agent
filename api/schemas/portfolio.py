"""Pydantic v2 request/response schemas for the portfolio CRUD endpoints."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TickerSearchResult(BaseModel):
    ticker: str
    name: str
    asset_type: Literal["stock", "etf", "crypto", "commodity", "bond"]


class TickerQuoteResponse(BaseModel):
    ticker: str
    price: Optional[float]
    currency: str


class PortfolioHoldingCreate(BaseModel):
    ticker: str = Field(
        min_length=1,
        max_length=20,
        description="Yahoo Finance ticker symbol (e.g. AAPL, BTC-USD, GC=F)",
    )
    name: str = Field(
        min_length=1,
        max_length=100,
        description="Human-readable asset name (e.g. Apple Inc.)",
    )
    asset_type: Literal["stock", "etf", "crypto", "commodity", "bond"]
    quantity: Optional[float] = Field(default=None, gt=0, description="Number of shares/units held")
    cost_basis_usd: Optional[float] = Field(default=None, gt=0, description="User's recorded entry value in USD")

    @field_validator("ticker", mode="before")
    @classmethod
    def uppercase_ticker(cls, v: str) -> str:
        return v.strip().upper()


class PortfolioHoldingUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    quantity: Optional[float] = Field(default=None, gt=0)
    cost_basis_usd: Optional[float] = Field(default=None, gt=0)


class PortfolioHoldingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticker: str
    name: str
    asset_type: str
    quantity: Optional[float]
    cost_basis_usd: Optional[float]
    last_price_usd: Optional[float]
    price_updated_at: Optional[str]
    created_at: str

    @classmethod
    def from_orm_model(cls, h: object) -> "PortfolioHoldingResponse":
        return cls(
            id=str(h.id),  # type: ignore[attr-defined]
            ticker=h.ticker,  # type: ignore[attr-defined]
            name=h.name,  # type: ignore[attr-defined]
            asset_type=h.asset_type,  # type: ignore[attr-defined]
            quantity=float(h.quantity) if h.quantity is not None else None,  # type: ignore[attr-defined]
            cost_basis_usd=float(h.cost_basis_usd) if h.cost_basis_usd is not None else None,  # type: ignore[attr-defined]
            last_price_usd=float(h.last_price_usd) if h.last_price_usd is not None else None,  # type: ignore[attr-defined]
            price_updated_at=h.price_updated_at.isoformat() if h.price_updated_at is not None else None,  # type: ignore[attr-defined]
            created_at=h.created_at.isoformat(),  # type: ignore[attr-defined]
        )
