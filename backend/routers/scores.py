"""
/scores — current scores list + per-ticker history.
"""
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from backend.database import get_db
from backend.models import ScoreSnapshot
from backend.schemas import ScoreRecord

router = APIRouter(prefix="/scores", tags=["scores"])


@router.get("", response_model=list[ScoreRecord])
def list_scores(db: Session = Depends(get_db)):
    """Return the latest snapshot per ticker, sorted by final_score desc."""
    from sqlalchemy import func
    subq = (
        select(
            ScoreSnapshot.ticker,
            func.max(ScoreSnapshot.snapshot_date).label("max_date"),
        )
        .group_by(ScoreSnapshot.ticker)
        .subquery()
    )
    rows = db.execute(
        select(ScoreSnapshot).join(
            subq,
            and_(
                ScoreSnapshot.ticker == subq.c.ticker,
                ScoreSnapshot.snapshot_date == subq.c.max_date,
            ),
        ).order_by(ScoreSnapshot.final_score.desc())
    ).scalars().all()
    return [_to_record(r) for r in rows]


@router.get("/{ticker}/history", response_model=list[ScoreRecord])
def get_history(
    ticker: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    cutoff = date.today() - timedelta(days=days)
    rows = db.execute(
        select(ScoreSnapshot)
        .where(
            and_(
                ScoreSnapshot.ticker == ticker.upper(),
                ScoreSnapshot.snapshot_date >= cutoff,
            )
        )
        .order_by(ScoreSnapshot.snapshot_date)
    ).scalars().all()
    return [_to_record(r) for r in rows]


def _to_record(r: ScoreSnapshot) -> ScoreRecord:
    return ScoreRecord(
        ticker=r.ticker,
        final_score=r.final_score,
        raw_score=r.raw_score,
        valuation_score=r.valuation_score,
        growth_score=r.growth_score,
        quality_score=r.quality_score,
        ai_exposure_score=r.ai_exposure_score,
        expectation_gap_score=r.expectation_gap_score,
        risk_penalty=r.risk_penalty,
        rating=r.rating,
        confidence_grade=r.confidence_grade,
        data_source=r.data_source,
        snapshot_date=r.snapshot_date,
    )
