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
    value_usd: Optional[float] = Field(default=None, gt=0, description="Position value in USD")

    @field_validator("ticker", mode="before")
    @classmethod
    def uppercase_ticker(cls, v: str) -> str:
        return v.strip().upper()


class PortfolioHoldingUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    quantity: Optional[float] = Field(default=None, gt=0)
    value_usd: Optional[float] = Field(default=None, gt=0)


class PortfolioHoldingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ticker: str
    name: str
    asset_type: str
    quantity: Optional[float]
    value_usd: Optional[float]
    created_at: str

    @classmethod
    def from_orm_model(cls, h: object) -> "PortfolioHoldingResponse":
        return cls(
            id=str(h.id),  # type: ignore[attr-defined]
            ticker=h.ticker,  # type: ignore[attr-defined]
            name=h.name,  # type: ignore[attr-defined]
            asset_type=h.asset_type,  # type: ignore[attr-defined]
            quantity=float(h.quantity) if h.quantity is not None else None,  # type: ignore[attr-defined]
            value_usd=float(h.value_usd) if h.value_usd is not None else None,  # type: ignore[attr-defined]
            created_at=h.created_at.isoformat(),  # type: ignore[attr-defined]
        )
