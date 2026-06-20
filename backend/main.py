"""
FastAPI main app — initialises DB tables and starts APScheduler on startup.
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func

from backend.database import init_db, is_db_available, SessionLocal
from backend.models import MarketDataCache
from backend.routers import overrides, market_data, scores
from backend.scheduler import create_scheduler
from backend.schemas import HealthResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Startup: creating DB tables")
    init_db()
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started")
    yield
    scheduler.shutdown(wait=False)
    logger.info("Shutdown complete")


app = FastAPI(
    title="AI Valuation Scoring API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(overrides.router)
app.include_router(market_data.router)
app.include_router(scores.router)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health():
    db_ok = is_db_available()
    cache_count = 0
    oldest_age_hours = None
    if db_ok:
        try:
            with SessionLocal() as db:
                cache_count = db.execute(
                    select(func.count()).select_from(MarketDataCache)
                ).scalar() or 0
                oldest_ts = db.execute(
                    select(func.min(MarketDataCache.fetched_at))
                ).scalar()
                if oldest_ts:
                    delta = datetime.now() - oldest_ts.replace(tzinfo=None)
                    oldest_age_hours = round(delta.total_seconds() / 3600, 1)
        except Exception:
            pass
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db_available=db_ok,
        cache_tickers=cache_count,
        oldest_cache_age_hours=oldest_age_hours,
    )
