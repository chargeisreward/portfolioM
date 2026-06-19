"""fill_prices_tencent.py — fetch current_price via Tencent for missing snapshot rows."""
import argparse
import logging
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from services.price_filler import fill_prices_for_as_of


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of-date", required=True)
    ap.add_argument("--max-codes", type=int, default=200)
    args = ap.parse_args()
    as_of = _date.fromisoformat(args.as_of_date)
    db = SessionLocal()
    try:
        result = fill_prices_for_as_of(db, as_of, max_codes=args.max_codes)
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()