"""
SQLAlchemy engine and session factory.
Shared by FastAPI routers and scoring/db_client.py.
Engine is created lazily on first use so that DATABASE_URL set after
module import (e.g. via a .env file or shell env) is picked up correctly.
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def _get_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    url = _get_url()
    if not url:
        return None
    _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _engine


def _get_session_factory():
    global _SessionLocal
    if _SessionLocal is not None:
        return _SessionLocal
    eng = _get_engine()
    if eng is None:
        return None
    _SessionLocal = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    return _SessionLocal


class SessionLocal:
    """Context-manager wrapper — usage: `with SessionLocal() as db: ...`"""
    def __new__(cls):
        factory = _get_session_factory()
        if factory is None:
            raise RuntimeError("DATABASE_URL not configured")
        return factory()


def get_db():
    """FastAPI dependency — yields a DB session."""
    factory = _get_session_factory()
    if factory is None:
        raise RuntimeError("DATABASE_URL not configured")
    db = factory()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist. Called on FastAPI startup."""
    eng = _get_engine()
    if eng is None:
        return
    from backend.models import UserOverride, MarketDataCache, ScoreSnapshot  # noqa: F401
    Base.metadata.create_all(bind=eng)


def is_db_available() -> bool:
    eng = _get_engine()
    if eng is None:
        return False
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
