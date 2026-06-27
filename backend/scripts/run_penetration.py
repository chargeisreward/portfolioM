"""CLI: run penetration for an as-of-date."""
import argparse
import logging
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from services.penetration_v2 import run_penetration_all_users


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of-date", required=True)
    args = ap.parse_args()
    as_of = _date.fromisoformat(args.as_of_date)
    db = SessionLocal()
    try:
        rep = run_penetration_all_users(db, as_of)
        print(f"as_of_date={rep.as_of_date}")
        print(f"holdings_seen={rep.holdings_seen}")
        print(f"holdings_drilled={rep.holdings_drilled}")
        print(f"holdings_skipped={len(rep.holdings_skipped)}")
        if rep.holdings_skipped:
            for s in rep.holdings_skipped:
                print(f"  SKIP {s}")
        print(f"penetration_snapshot rows={rep.rows_inserted_pnsnap}")
        print(f"full_holding_snapshot rows={rep.rows_inserted_fhsnap}")
        if rep.errors:
            for e in rep.errors:
                print(f"  ERR: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()