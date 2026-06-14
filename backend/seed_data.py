"""Seed database with index constituent data and financial data.

Uses akshare to fetch real index constituents and basic financial data.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from database import init_db, SessionLocal
from models import IndexConstituent, Fund, StockFinancial, PenetrationResult

init_db()
db = SessionLocal()


def seed_constituents():
    """Fetch index constituents via akshare and seed the DB."""
    try:
        import akshare as ak
    except ImportError:
        print("akshare not installed")
        return 0

    # Index code mapping: our_code -> akshare code
    index_map = {}
    funds = db.query(Fund).filter(Fund.tracking_index_code.isnot(None)).all()
    for f in funds:
        idx = f.tracking_index_code
        if idx and len(idx) == 6:
            # CSI indices use sh/sz prefix
            index_map[idx] = f"sh{idx}"

    # Always add CSI 300 for comparison
    index_map["000300"] = "sh000300"

    count = 0
    from datetime import date
    as_of = date.today()

    for idx_code, ak_code in sorted(index_map.items()):
        try:
            df = ak.index_stock_cons(symbol=ak_code)
            if df is None or df.empty:
                continue

            # Delete old data
            db.query(IndexConstituent).filter(
                IndexConstituent.index_code == idx_code
            ).delete()

            # Calculate total weight if not present
            has_weight = "权重" in df.columns
            total = df["权重"].sum() if has_weight else len(df)

            for _, row in df.iterrows():
                weight = float(row["权重"] / total * 100) if has_weight else (1 / total * 100) if total > 0 else 0
                stock_code = str(row.get("品种代码", row.get("stock_code", "")))
                stock_name = str(row.get("品种名称", row.get("stock_name", "")))

                if stock_code:
                    c = IndexConstituent(
                        index_code=idx_code,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        weight=round(weight, 4),
                        as_of_date=as_of,
                    )
                    db.add(c)
                    count += 1

            db.commit()
            print(f"  {idx_code}: {len(df)} constituents")
        except Exception as e:
            db.rollback()
            print(f"  {idx_code}: failed - {e}")

    db.commit()
    print(f"Total constituents seeded: {count}")
    return count


def seed_financials():
    """Fetch financial data for CSI 300 constituents to enable comparison."""
    try:
        import akshare as ak
    except ImportError:
        return

    # Get CSI300 stocks
    csi300 = db.query(IndexConstituent).filter(
        IndexConstituent.index_code == "000300"
    ).all()

    if not csi300:
        print("No CSI 300 constituents found, skipping financials")
        return

    from datetime import date
    as_of = date.today()
    count = 0

    for c in csi300[:50]:  # Limit to 50 to avoid rate limits
        try:
            # Try to get real-time basic data
            df = ak.stock_individual_info_em(symbol=c.stock_code)
            if df is not None and not df.empty:
                info = {}
                for _, row in df.iterrows():
                    info[str(row["item"])] = row["value"]

                pe = info.get("市盈率-动态")
                mc = info.get("总市值")

                existing = db.query(StockFinancial).filter(
                    StockFinancial.stock_code == c.stock_code,
                    StockFinancial.as_of_date == as_of,
                ).first()

                if not existing:
                    sf = StockFinancial(
                        stock_code=c.stock_code,
                        stock_name=c.stock_name,
                        ttm_pe=float(pe) if pe else None,
                        market_cap=float(mc.replace("亿", "")) if mc else None,
                        industry_sw="",
                        as_of_date=as_of,
                    )
                    db.add(sf)
                    count += 1
        except Exception:
            pass

    db.commit()
    print(f"Financial data seeded for {count} stocks")


if __name__ == "__main__":
    print("Seeding index constituents...")
    n = seed_constituents()

    print("\nSeeding financial data...")
    seed_financials()

    db.close()
    print("\nDone! Run /api/penetration/calculate to refresh.")
