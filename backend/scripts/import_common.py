"""
Common helpers for the snapshot importers (spec §2).

Each importer is idempotent on (as_of_date, ...). It reads an Excel
file under `sourceData/{folder}/`, normalizes column headers, and
bulk-writes rows into the corresponding snapshot table.

Dynamic price columns (`pe_ttm_dynamic`, `pb_mrq_dynamic`, `ps_ttm_dynamic`)
are computed at import time from `price_cache`:

    dynamic = baseline * (current_price / baseline_price)

where baseline_price is the close on `as_of_date`, and current_price is the
prev trading-day close. If either is missing the dynamic column is left NULL.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from database import SessionLocal
from models import PriceCache

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    as_of_date: _date
    table: str
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"[{self.table}] as_of={self.as_of_date} "
            f"inserted={self.rows_inserted} skipped={self.rows_skipped} "
            f"errors={len(self.errors)}"
        )


# --------------------------------------------------------------------------
# Header normalization
# --------------------------------------------------------------------------

def _normalize(col: Any) -> str:
    """Excel column headers contain line-breaks; take the first line only."""
    if col is None:
        return ""
    s = str(col).split("\n")[0].strip()
    return s


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_normalize(c) for c in df.columns]
    return df


def find_column(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    """Return the first column name present in df that matches any candidate."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


# --------------------------------------------------------------------------
# Price lookup
# --------------------------------------------------------------------------

def _latest_close_on_or_before(db: Session, stock_code: str, on_or_before: _date):
    """Return (close_px, trade_date) for the latest trading day <= on_or_before."""
    row = (
        db.query(PriceCache)
        .filter(PriceCache.stock_code == stock_code)
        .filter(PriceCache.trade_date <= on_or_before)
        .filter(PriceCache.close_px.isnot(None))
        .order_by(PriceCache.trade_date.desc())
        .first()
    )
    if row:
        return row.close_px, row.trade_date
    return None, None


def _prev_close(db: Session, stock_code: str, today: _date):
    """Return (close_px, trade_date) of the latest trading day strictly < today."""
    row = (
        db.query(PriceCache)
        .filter(PriceCache.stock_code == stock_code)
        .filter(PriceCache.trade_date < today)
        .filter(PriceCache.close_px.isnot(None))
        .order_by(PriceCache.trade_date.desc())
        .first()
    )
    if row:
        return row.close_px, row.trade_date
    return None, None


def resolve_price_pair(db: Session, stock_code: str, as_of_date: _date):
    """Resolve (baseline_price, current_price, current_price_date)."""
    baseline, _ = _latest_close_on_or_before(db, stock_code, as_of_date)
    current, current_date = _prev_close(db, stock_code, _date.today())
    return baseline, current, current_date


def compute_dynamic(metric_baseline: float | None,
                    baseline_price: float | None,
                    current_price: float | None) -> float | None:
    """dynamic = baseline * (current_price / baseline_price)."""
    if not metric_baseline or not baseline_price or not current_price:
        return None
    if baseline_price <= 0:
        return None
    try:
        v = metric_baseline * (current_price / baseline_price)
        # Sanitize
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return round(float(v), 4)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Idempotent bulk delete-by-as-of
# --------------------------------------------------------------------------

def wipe_as_of(db: Session, model, as_of_date: _date, *, extra_filters: list | None = None) -> int:
    """Delete all rows of `model` for `as_of_date` and return deleted count."""
    q = db.query(model).filter(model.as_of_date == as_of_date)
    if extra_filters:
        for f in extra_filters:
            q = q.filter(f)
    deleted = q.delete(synchronize_session=False)
    db.commit()
    return deleted


def safe_to_float(x) -> float | None:
    if x is None:
        return None
    if isinstance(x, str):
        s = x.strip()
        if not s or s in ("--", "—", "—", "N/A", "n/a", "None"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    try:
        f = float(x)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def read_excel(path: Path) -> pd.DataFrame:
    return pd.read_excel(path)


def session_scope():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()