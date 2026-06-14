"""Load financial seed data"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from database import init_db, SessionLocal
from models import StockFinancial

init_db()
db = SessionLocal()
today = date.today()

csv_path = os.path.join(os.path.dirname(__file__), "data", "seed_financials.csv")
count = 0
with open(csv_path, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        code = row["stock_code"].strip()
        existing = db.query(StockFinancial).filter(StockFinancial.stock_code == code).first()
        if existing:
            existing.ttm_pe = float(row["ttm_pe"]) if row["ttm_pe"] else None
            existing.profit_growth = float(row["profit_growth"]) if row["profit_growth"] else None
            existing.revenue_growth = float(row["revenue_growth"]) if row["revenue_growth"] else None
            existing.industry_sw = row["industry_sw"]
            existing.stock_name = row["stock_name"]
        else:
            sf = StockFinancial(
                stock_code=code, stock_name=row["stock_name"],
                ttm_pe=float(row["ttm_pe"]) if row["ttm_pe"] else None,
                profit_growth=float(row["profit_growth"]) if row["profit_growth"] else None,
                revenue_growth=float(row["revenue_growth"]) if row["revenue_growth"] else None,
                industry_sw=row["industry_sw"], as_of_date=today,
            )
            db.add(sf)
        count += 1
db.commit()
db.close()
print(f"Seeded {count} financial records")
