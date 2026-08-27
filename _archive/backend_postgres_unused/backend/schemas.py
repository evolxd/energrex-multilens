"""
Pydantic v2 request/response schemas for FastAPI.
"""
from datetime import datetime, date
from typing import Any
from pydantic import BaseModel


class OverrideItem(BaseModel):
    ticker: str
    field_name: str
    value: float
    updated_at: datetime


class OverrideValue(BaseModel):
    value: float


class CacheEntry(BaseModel):
    ticker: str
    fetched_at: datetime
    data: dict[str, Any]


class CacheSummary(BaseModel):
    ticker: str
    fetched_at: datetime
    field_count: int


class ScoreRecord(BaseModel):
    ticker: str
    final_score: float
    raw_score: float | None
    valuation_score: float | None
    growth_score: float | None
    quality_score: float | None
    ai_exposure_score: float | None
    expectation_gap_score: float | None
    risk_penalty: float | None
    rating: str | None
    confidence_grade: str | None
    data_source: str
    snapshot_date: date


class HealthResponse(BaseModel):
    status: str
    db_available: bool
    cache_tickers: int
    oldest_cache_age_hours: float | None
