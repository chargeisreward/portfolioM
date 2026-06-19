"""
Data version meta-table (spec §2.1).

Reads `sourceData/data_version.csv` and resolves the active
`as_of_date` for a given `today`:

    business_date = MAX(as_of_date) WHERE as_of_date <= today
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DATA_DIR = PROJECT_ROOT / "sourceData"
DATA_VERSION_CSV = SOURCE_DATA_DIR / "data_version.csv"


@dataclass(frozen=True)
class DataVersion:
    as_of_date: _date
    source_folder: str
    imported_at: str
    note: str


def list_available_versions() -> list[DataVersion]:
    """Return all versions listed in data_version.csv, sorted ASC."""
    if not DATA_VERSION_CSV.exists():
        logger.warning("data_version.csv not found at %s", DATA_VERSION_CSV)
        return []
    out: list[DataVersion] = []
    with DATA_VERSION_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = _date.fromisoformat(row["as_of_date"])
            except (KeyError, ValueError):
                continue
            out.append(
                DataVersion(
                    as_of_date=d,
                    source_folder=row.get("source_folder", ""),
                    imported_at=row.get("imported_at", ""),
                    note=row.get("note", ""),
                )
            )
    out.sort(key=lambda v: v.as_of_date)
    return out


def current_business_date(today: _date | None = None) -> _date | None:
    """
    MAX(as_of_date) WHERE as_of_date <= today.
    If `today` is None, uses date.today().
    Returns None if no version is registered yet.
    """
    if today is None:
        today = _date.today()
    candidates = [v.as_of_date for v in list_available_versions() if v.as_of_date <= today]
    return max(candidates) if candidates else None


def resolve_source_folder(as_of_date: _date) -> Path | None:
    """Return the source folder for a given as_of_date, or None if unknown."""
    for v in list_available_versions():
        if v.as_of_date == as_of_date:
            return SOURCE_DATA_DIR / v.source_folder
    return None