"""
/market-data — yfinance cache read + manual refresh trigger.
"""
import asyncio
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.database import get_db
from backend.models import MarketDataCache
from backend.schemas import CacheSummary

router = APIRouter(prefix="/market-data", tags=["market-data"])

def _get_tickers() -> list[str]:
    try:
        import sys, os
        _scoring = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scoring"))
        if _scoring not in sys.path:
            sys.path.insert(0, _scoring)
        from mock_data import MOCK_STOCKS
        return list(MOCK_STOCKS.keys())
    except Exception:
        return ["NVDA", "AVGO", "MRVL", "PLTR", "SNOW", "NOW", "PANW", "CRWD", "FTNT", "ONTO"]

TICKERS: list[str] = _get_tickers()


@router.get("/cache", response_model=list[CacheSummary])
def list_cache(db: Session = Depends(get_db)):
    rows = db.execute(select(MarketDataCache)).scalars().all()
    return [
        CacheSummary(
            ticker=row.ticker,
            fetched_at=row.fetched_at,
            field_count=sum(1 for k in row.data if not k.startswith("_")),
        )
        for row in rows
    ]


@router.post("/refresh", status_code=202)
def trigger_refresh(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Trigger a background yfinance fetch for all tickers."""
    background_tasks.add_task(_refresh_cache)
    return {"status": "refresh_queued", "tickers": TICKERS}


def _refresh_cache():
    import sys, os
    _here = os.path.dirname(os.path.abspath(__file__))       # .../backend/routers
    _root = os.path.abspath(os.path.join(_here, "..", "..")) # .../ai_valuation
    _scoring = os.path.join(_root, "scoring")
    if _root not in sys.path:
        sys.path.insert(0, _root)
    if _scoring not in sys.path:
        sys.path.insert(0, _scoring)
    from yfinance_fetcher import fetch_portfolio_live_parallel
    from backend.database import SessionLocal
    live_all = fetch_portfolio_live_parallel(TICKERS)
    now = datetime.now()
    with SessionLocal() as db:
        for ticker, data in live_all.items():
            stmt = pg_insert(MarketDataCache).values(
                ticker=ticker,
                data=data,
                fetched_at=now,
            ).on_conflict_do_update(
                index_elements=["ticker"],
                set_={"data": data, "fetched_at": now},
            )
            db.execute(stmt)
        db.commit()
