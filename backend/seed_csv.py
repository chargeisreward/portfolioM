"""Quick seed: load constituents from CSV, skip network calls."""
import sys, os, csv
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from database import init_db, SessionLocal
from models import IndexConstituent

init_db()
db = SessionLocal()
today = date.today()

csv_path = os.path.join(os.path.dirname(__file__), "data", "seed_constituents.csv")
count = 0
with open(csv_path, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        idx = row["index_code"].strip()
        code = row["stock_code"].strip()
        name = row["stock_name"].strip()
        w = float(row["weight"])
        existing = db.query(IndexConstituent).filter(
            IndexConstituent.index_code == idx,
            IndexConstituent.stock_code == code,
        ).first()
        if existing:
            existing.weight = w
        else:
            c = IndexConstituent(index_code=idx, stock_code=code, stock_name=name, weight=w, as_of_date=today)
            db.add(c)
        count += 1

db.commit()
stocks = db.query(IndexConstituent.stock_code).distinct().count()
indices = db.query(IndexConstituent.index_code).distinct().count()
print(f"Loaded {count} records: {stocks} stocks in {indices} indices")
db.close()
