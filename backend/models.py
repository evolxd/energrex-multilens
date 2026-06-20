"""
SQLAlchemy ORM models — mirrors init.sql exactly.
"""
from datetime import datetime, date
from sqlalchemy import String, Float, DateTime, Date, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from backend.database import Base


class UserOverride(Base):
    __tablename__ = "user_overrides"

    id:         Mapped[int]      = mapped_column(primary_key=True)
    ticker:     Mapped[str]      = mapped_column(String(10), nullable=False)
    field_name: Mapped[str]      = mapped_column(String(60), nullable=False)
    value:      Mapped[float]    = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                  server_default=func.now(),
                                                  onupdate=func.now())


class MarketDataCache(Base):
    __tablename__ = "market_data_cache"

    ticker:     Mapped[str]      = mapped_column(String(10), primary_key=True)
    data:       Mapped[dict]     = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id:                    Mapped[int]      = mapped_column(primary_key=True)
    ticker:                Mapped[str]      = mapped_column(String(10), nullable=False)
    snapshot_date:         Mapped[date]     = mapped_column(Date, nullable=False,
                                                             server_default=func.current_date())
    final_score:           Mapped[float]    = mapped_column(Float, nullable=False)
    raw_score:             Mapped[float]    = mapped_column(Float, nullable=True)
    valuation_score:       Mapped[float]    = mapped_column(Float, nullable=True)
    growth_score:          Mapped[float]    = mapped_column(Float, nullable=True)
    quality_score:         Mapped[float]    = mapped_column(Float, nullable=True)
    ai_exposure_score:     Mapped[float]    = mapped_column(Float, nullable=True)
    expectation_gap_score: Mapped[float]    = mapped_column(Float, nullable=True)
    risk_penalty:          Mapped[float]    = mapped_column(Float, nullable=True)
    rating:                Mapped[str]      = mapped_column(String(30), nullable=True)
    confidence_grade:      Mapped[str]      = mapped_column(String(1), nullable=True)
    data_source:           Mapped[str]      = mapped_column(String(10), nullable=False,
                                                             server_default="live")
    created_at:            Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                             server_default=func.now())
