"""
/overrides — CRUD API for user override values.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import datetime

from backend.database import get_db
from backend.models import UserOverride
from backend.schemas import OverrideItem, OverrideValue

router = APIRouter(prefix="/overrides", tags=["overrides"])


@router.get("", response_model=dict[str, dict[str, float]])
def list_all_overrides(db: Session = Depends(get_db)):
    rows = db.execute(select(UserOverride)).scalars().all()
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        result.setdefault(row.ticker, {})[row.field_name] = row.value
    return result


@router.get("/{ticker}", response_model=dict[str, float])
def get_ticker_overrides(ticker: str, db: Session = Depends(get_db)):
    rows = db.execute(
        select(UserOverride).where(UserOverride.ticker == ticker.upper())
    ).scalars().all()
    return {row.field_name: row.value for row in rows}


@router.put("/{ticker}/{field}", response_model=OverrideItem)
def upsert_override(
    ticker: str,
    field: str,
    body: OverrideValue,
    db: Session = Depends(get_db),
):
    ticker = ticker.upper()
    now = datetime.now()
    stmt = pg_insert(UserOverride).values(
        ticker=ticker,
        field_name=field,
        value=body.value,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=["ticker", "field_name"],
        set_={"value": body.value, "updated_at": now},
    ).returning(UserOverride)
    row = db.execute(stmt).fetchone()
    db.commit()
    return OverrideItem(
        ticker=ticker,
        field_name=field,
        value=body.value,
        updated_at=now,
    )


@router.delete("/{ticker}/{field}", status_code=204)
def delete_field_override(ticker: str, field: str, db: Session = Depends(get_db)):
    db.execute(
        delete(UserOverride).where(
            UserOverride.ticker == ticker.upper(),
            UserOverride.field_name == field,
        )
    )
    db.commit()


@router.delete("/{ticker}", status_code=204)
def delete_ticker_overrides(ticker: str, db: Session = Depends(get_db)):
    db.execute(
        delete(UserOverride).where(UserOverride.ticker == ticker.upper())
    )
    db.commit()
