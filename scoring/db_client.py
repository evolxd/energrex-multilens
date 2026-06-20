"""
DB access layer — Streamlit to PostgreSQL.
All database calls from app.py go through this module.
Falls back to JSON file automatically when DATABASE_URL is not set.
"""
import json
import pathlib
import sys
from datetime import datetime
from typing import Optional

_JSON_PATH = pathlib.Path(__file__).parent / "user_overrides.json"

# Attempt to load SQLAlchemy; gracefully degrade if unavailable or DB unreachable.
try:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from backend.database import is_db_available, SessionLocal
    from backend.models import UserOverride, MarketDataCache, ScoreSnapshot
    from sqlalchemy import select, delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False


def is_available() -> bool:
    """Return True if the database is reachable."""
    if not _SA_AVAILABLE:
        return False
    return is_db_available()


# ── User Overrides ─────────────────────────────────────────

def get_all_overrides() -> dict[str, dict[str, float]]:
    """Return all overrides as {ticker: {field_name: value}}. Falls back to JSON."""
    if is_available():
        try:
            with SessionLocal() as db:
                rows = db.execute(select(UserOverride)).scalars().all()
            result: dict[str, dict[str, float]] = {}
            for row in rows:
                result.setdefault(row.ticker, {})[row.field_name] = row.value
            return result
        except Exception:
            pass
    return _json_load()


def upsert_ticker_overrides(ticker: str, changes: dict[str, float]) -> None:
    """Upsert changed fields for a ticker into DB (or JSON fallback)."""
    if is_available():
        try:
            with SessionLocal() as db:
                for field_name, value in changes.items():
                    stmt = pg_insert(UserOverride).values(
                        ticker=ticker,
                        field_name=field_name,
                        value=float(value),
                        updated_at=datetime.now(),
                    ).on_conflict_do_update(
                        index_elements=["ticker", "field_name"],
                        set_={"value": float(value), "updated_at": datetime.now()},
                    )
                    db.execute(stmt)
                db.commit()
            return
        except Exception:
            pass
    data = _json_load()
    data.setdefault(ticker, {}).update(changes)
    _json_save(data)


def clear_ticker_overrides(ticker: str) -> None:
    """Delete all overrides for a ticker."""
    if is_available():
        try:
            with SessionLocal() as db:
                db.execute(delete(UserOverride).where(UserOverride.ticker == ticker))
                db.commit()
            return
        except Exception:
            pass
    data = _json_load()
    data.pop(ticker, None)
    _json_save(data)


# ── Market Data Cache ──────────────────────────────────────

def get_live_cache() -> dict[str, dict]:
    """Return cached yfinance data from DB. Returns empty dict if unavailable."""
    if not is_available():
        return {}
    try:
        with SessionLocal() as db:
            rows = db.execute(select(MarketDataCache)).scalars().all()
        return {row.ticker: row.data for row in rows}
    except Exception:
        return {}


def get_cache_fetched_at() -> Optional[datetime]:
    """Return the oldest fetched_at timestamp across all cached tickers, or None."""
    if not is_available():
        return None
    try:
        from sqlalchemy import func as sa_func
        with SessionLocal() as db:
            row = db.execute(
                select(sa_func.min(MarketDataCache.fetched_at))
            ).scalar()
        if row is None:
            return None
        # Normalise to naive datetime
        if hasattr(row, "replace"):
            return row.replace(tzinfo=None) if row.tzinfo else row
        return row
    except Exception:
        return None


def write_live_cache(live_all: dict[str, dict], fetched_at: datetime) -> None:
    """Upsert yfinance results into market_data_cache, one row per ticker."""
    if not is_available():
        return
    try:
        with SessionLocal() as db:
            for ticker, data in live_all.items():
                stmt = pg_insert(MarketDataCache).values(
                    ticker=ticker,
                    data=data,
                    fetched_at=fetched_at,
                ).on_conflict_do_update(
                    index_elements=["ticker"],
                    set_={"data": data, "fetched_at": fetched_at},
                )
                db.execute(stmt)
            db.commit()
    except Exception:
        pass


# ── Score History ──────────────────────────────────────────

def get_score_history(ticker: str, days: int = 30) -> list[dict]:
    """Return score snapshots for the last N days, ordered by date ascending."""
    if not is_available():
        return []
    try:
        from sqlalchemy import and_
        from datetime import timedelta
        cutoff = datetime.now().date() - timedelta(days=days)
        with SessionLocal() as db:
            rows = db.execute(
                select(ScoreSnapshot)
                .where(
                    and_(
                        ScoreSnapshot.ticker == ticker,
                        ScoreSnapshot.snapshot_date >= cutoff,
                    )
                )
                .order_by(ScoreSnapshot.snapshot_date)
            ).scalars().all()
        return [
            {
                "snapshot_date":         str(r.snapshot_date),
                "final_score":           r.final_score,
                "raw_score":             r.raw_score,
                "valuation_score":       r.valuation_score,
                "growth_score":          r.growth_score,
                "quality_score":         r.quality_score,
                "ai_exposure_score":     r.ai_exposure_score,
                "expectation_gap_score": r.expectation_gap_score,
                "risk_penalty":          r.risk_penalty,
                "rating":                r.rating,
                "data_source":           r.data_source,
            }
            for r in rows
        ]
    except Exception:
        return []


def write_score_snapshots(scores_df) -> None:
    """Upsert score_portfolio() DataFrame into score_snapshots. Called by APScheduler."""
    if not is_available():
        return
    try:
        today = datetime.now().date()
        with SessionLocal() as db:
            for _, row in scores_df.iterrows():
                stmt = pg_insert(ScoreSnapshot).values(
                    ticker=row["ticker"],
                    snapshot_date=today,
                    final_score=float(row["final_score"]),
                    raw_score=float(row.get("raw_score", 0) or 0),
                    valuation_score=float(row.get("valuation_score", 0) or 0),
                    growth_score=float(row.get("growth_score", 0) or 0),
                    quality_score=float(row.get("quality_score", 0) or 0),
                    ai_exposure_score=float(row.get("ai_exposure_score", 0) or 0),
                    expectation_gap_score=float(row.get("expectation_gap_score", 0) or 0),
                    risk_penalty=float(row.get("risk_penalty", 0) or 0),
                    rating=str(row.get("rating", "") or ""),
                    confidence_grade=str(row.get("confidence_grade", "") or ""),
                    data_source="live",
                ).on_conflict_do_update(
                    index_elements=["ticker", "snapshot_date"],
                    set_={
                        "final_score":           float(row["final_score"]),
                        "raw_score":             float(row.get("raw_score", 0) or 0),
                        "valuation_score":       float(row.get("valuation_score", 0) or 0),
                        "growth_score":          float(row.get("growth_score", 0) or 0),
                        "quality_score":         float(row.get("quality_score", 0) or 0),
                        "ai_exposure_score":     float(row.get("ai_exposure_score", 0) or 0),
                        "expectation_gap_score": float(row.get("expectation_gap_score", 0) or 0),
                        "risk_penalty":          float(row.get("risk_penalty", 0) or 0),
                        "rating":                str(row.get("rating", "") or ""),
                        "confidence_grade":      str(row.get("confidence_grade", "") or ""),
                        "data_source":           "live",
                    },
                )
                db.execute(stmt)
            db.commit()
    except Exception:
        pass


# ── One-time JSON → DB migration ──────────────────────────

def seed_from_json() -> int:
    """Migrate user_overrides.json into the DB (skip existing rows). Returns count inserted."""
    if not is_available():
        print("DB unavailable — check DATABASE_URL.")
        return 0
    data = _json_load()
    if not data:
        print("Nothing to migrate.")
        return 0
    count = 0
    with SessionLocal() as db:
        for ticker, fields in data.items():
            for field_name, value in fields.items():
                stmt = pg_insert(UserOverride).values(
                    ticker=ticker,
                    field_name=field_name,
                    value=float(value),
                    updated_at=datetime.now(),
                ).on_conflict_do_nothing(index_elements=["ticker", "field_name"])
                db.execute(stmt)
                count += 1
        db.commit()
    print(f"Migration complete: {count} rows inserted.")
    return count


# ── JSON helpers ───────────────────────────────────────────

def _json_load() -> dict:
    try:
        return json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _json_save(data: dict) -> None:
    _JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── CLI: python -m scoring.db_client --status / --seed ────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DB utility")
    parser.add_argument("--seed",   action="store_true", help="Migrate JSON overrides to DB")
    parser.add_argument("--status", action="store_true", help="Show DB connection status")
    args = parser.parse_args()

    if args.status:
        avail = is_available()
        print(f"DB available: {avail}")
        if avail:
            overrides = get_all_overrides()
            total_fields = sum(len(v) for v in overrides.values())
            print(f"Overrides: {len(overrides)} tickers, {total_fields} fields")
            ts = get_cache_fetched_at()
            print(f"Cache fetched_at (oldest): {ts}")
    elif args.seed:
        seed_from_json()
    else:
        parser.print_help()
