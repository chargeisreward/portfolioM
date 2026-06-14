"""Full pipeline integration test - run from backend/ dir"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))  # add backend/ to path
os.chdir(os.path.join(os.path.dirname(__file__), ".."))  # cd to project root

from database import init_db, SessionLocal
init_db()
db = SessionLocal()

# 1. Import
print("1. Importing Excel...")
from services.importer import import_excel
xlsx = [f for f in os.listdir(".") if f.endswith(".xlsx")][0]
count = import_excel(xlsx, db)
print(f"   {count} holdings imported")

# 2. ETF mapping
print("2. Mapping ETF to indices...")
from crawlers.etf_index import crawl_fund_index_map
funds = crawl_fund_index_map(db)
print(f"   {funds} funds mapped")

# 3. Penetration
print("3. Calculating penetration...")
from services.penetration import PenetrationEngine
engine = PenetrationEngine(db)
results = engine.calculate()
print(f"   {len(results)} underlying stocks")

# 4. Show top by weight
print("\n4. Top holdings:")
for r in sorted(results, key=lambda x: x.penetration_weight, reverse=True)[:10]:
    print(f"   {r.stock_code:10s} {r.stock_name or '':20s} {r.penetration_weight:.2f}%")

# 5. Growth distribution
print("\n5. Growth distribution:")
from services.growth_bucketer import GrowthBucketer
bucketer = GrowthBucketer(db)
dist = bucketer.compute_portfolio_growth_distribution({"high_cutoff": 20.0, "med_cutoff": 10.0})
print(f"   {dist}")

# 6. Industry chain
print("\n6. Industry chain:")
from services.growth_bucketer import IndustryChainAnalyzer
chain = IndustryChainAnalyzer.compute_distribution(results)
print(f"   {chain}")

db.close()
print("\n✅ Pipeline OK!")
