"""初始化交易日历（幂等，可重复运行）

用法（在 backend/ 目录下）：
    python scripts/init_calendar.py
    python scripts/init_calendar.py --start 2020 --end 2030
"""
import argparse
import sys
from pathlib import Path

# 把 backend/ 加进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import SessionLocal
from services.trading_calendar import populate_market, MARKETS


def main():
    parser = argparse.ArgumentParser(description="初始化 PortfolioM 交易日历")
    parser.add_argument("--start", type=int, default=2020, help="起始年份（默认 2020）")
    parser.add_argument("--end", type=int, default=2030, help="结束年份（默认 2030）")
    parser.add_argument("--markets", nargs="+", default=list(MARKETS), help=f"要初始化的市场，默认 {list(MARKETS)}")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total = 0
        for m in args.markets:
            n = populate_market(m, args.start, args.end, db)
            total += n
            print(f"  [{m}] 新增 {n} 行")
        print(f"完成。总计 {total} 行。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
