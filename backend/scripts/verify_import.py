"""verify_import.py — end-to-end verification harness (spec §7).

Run: python scripts/verify_import.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from models import (
    AShareFinancialSnapshot,
    AggregationCache,
    AggregationTimeseries,
    Csi300ConstituentSnapshot,
    FundIndexMap,
    FullHoldingSnapshot,
    HKShareFinancialSnapshot,
    IndexConstituentSnapshot,
    PenetrationSnapshot,
)
from services.aggregation import refresh_all_dimensions, write_timeseries_for_day
from services.data_version import current_business_date
from services.penetration_v2 import run_penetration


AS_OF = date(2026, 5, 29)


def main():
    db = SessionLocal()
    try:
        biz = current_business_date()
        print(f"current_business_date: {biz}")
        assert biz == AS_OF, f"expected {AS_OF}, got {biz}"

        print()
        print("=== Snapshot counts ===")
        counts = {
            "fund_index_map": db.query(FundIndexMap).count(),
            "index_constituent_snapshot": db.query(IndexConstituentSnapshot).count(),
            "a_share_financial_snapshot": db.query(AShareFinancialSnapshot).count(),
            "hk_share_financial_snapshot": db.query(HKShareFinancialSnapshot).count(),
            "csi300_constituent_snapshot": db.query(Csi300ConstituentSnapshot).count(),
        }
        for k, v in counts.items():
            print(f"  {k}: {v}")

        # Expected (per spec §7 step 2):
        #   a_share ~ 5533, hk_share ~ 2779, constituents ~ 5500 across 12 indices,
        #   fund_index_map = 22 (15 actually track an index)
        # We check relaxed bounds:
        assert counts["a_share_financial_snapshot"] >= 5000, "a_share too few"
        assert counts["hk_share_financial_snapshot"] >= 2000, "hk_share too few"
        assert counts["index_constituent_snapshot"] >= 1000, "constituents too few"
        assert counts["fund_index_map"] >= 10, "fund_index_map too few"

        # Penetration
        pn = run_penetration(db, AS_OF)
        print()
        print("=== Penetration ===")
        print(f"  holdings_seen: {pn.holdings_seen}")
        print(f"  holdings_drilled: {pn.holdings_drilled}")
        print(f"  rows_inserted_pnsnap: {pn.rows_inserted_pnsnap}")
        print(f"  rows_inserted_fhsnap: {pn.rows_inserted_fhsnap}")
        # The spec said ~300-500 per fund; just sanity check
        assert pn.rows_inserted_pnsnap > 500, "penetration rows too few"
        assert pn.rows_inserted_fhsnap > 500, "full_holding rows too few"

        # Aggregation
        refresh_all_dimensions(db, AS_OF)
        write_timeseries_for_day(db, AS_OF, AS_OF)

        agg_count = db.query(AggregationCache).filter(
            AggregationCache.as_of_date == AS_OF,
        ).count()
        ts_count = db.query(AggregationTimeseries).filter(
            AggregationTimeseries.calc_date == AS_OF,
        ).count()
        print()
        print("=== Aggregation ===")
        print(f"  aggregation_cache rows: {agg_count}")
        print(f"  aggregation_timeseries rows: {ts_count}")
        assert agg_count >= 20, "aggregation_cache rows too few"
        assert ts_count == 2, f"expected 2 timeseries rows (portfolio+csi300), got {ts_count}"

        # Spot-check virtual-earnings rule
        l1_total = db.query(AggregationCache).filter(
            AggregationCache.as_of_date == AS_OF,
            AggregationCache.scope == "portfolio",
            AggregationCache.dimension == "l1",
            AggregationCache.key == "_total",
        ).first()
        print(f"  portfolio total pe_weighted: {l1_total.pe_weighted}")
        # pe_weighted can be None when price_cache is empty (no dynamic metrics).
        # If non-None, confirm it's a positive finite number.
        if l1_total.pe_weighted is not None:
            assert l1_total.pe_weighted > 0, "pe_weighted should be positive"

        # KPI bar
        from models import FullHoldingSnapshot
        from sqlalchemy import func
        total_amount = db.query(func.coalesce(func.sum(FullHoldingSnapshot.amount_cny), 0)).filter(
            FullHoldingSnapshot.as_of_date == AS_OF,
        ).scalar() or 0
        drilled = db.query(func.count(func.distinct(FullHoldingSnapshot.stock_code))).filter(
            FullHoldingSnapshot.as_of_date == AS_OF,
        ).scalar() or 0
        print()
        print("=== KPI ===")
        print(f"  total_amount_cny: {total_amount:,.0f}")
        print(f"  drilled_stock_count: {drilled}")
        assert total_amount > 0, "no amount"
        assert drilled > 100, "drilled stocks too few"

        print()
        print("OK ALL CHECKS PASSED")
    finally:
        db.close()


if __name__ == "__main__":
    main()