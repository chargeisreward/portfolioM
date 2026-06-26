"""crawl_index_official.py — official-source crawler for index constituents (spec §2.6).

When akshare data is missing or stale, fall back to the official
provider endpoints:

  - CSI (中证指数)   : https://www.csindex.com.cn
  - CNINDEX (国证指数) : http://www.cnindex.com.cn
  - SZSE (深交所)    : http://www.szse.cn
  - HSI  (恒生指数)   : https://www.hsi.com.hk

Output: sourceData/{YYYYMM数据}/{index_code}.xlsx — one file per index,
which `import_index_constituents.py` can ingest.

NOTE: Each provider has different anti-scraping policies. In production,
network access may be limited (Zeabur sandbox). This script logs
attempts and errors; it is best-effort, not authoritative.

Usage:
    python scripts/crawl_index_official.py --provider csi --index 000300 \\
        --as-of-date 2026-05-29 --out sourceData/202605数据/000300.xlsx
    # 基础数据基准期5月29日
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _ok(out_path: Path, df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out_path, index=False)
    logger.info("wrote %s (%d rows)", out_path, len(df))
    return True


def crawl_csi(index_code: str, as_of: _date) -> pd.DataFrame | None:
    """CSI official — needs playwright/requests; placeholder returns None."""
    logger.warning("crawl_csi(%s) — official CSI endpoint not implemented; "
                   "use akshare download_index_cons.py for now", index_code)
    return None


def crawl_szse(index_code: str, as_of: _date) -> pd.DataFrame | None:
    """SZSE — needs playwright; placeholder."""
    logger.warning("crawl_szse(%s) — official SZSE endpoint not implemented", index_code)
    return None


def crawl_hsi(index_code: str, as_of: _date) -> pd.DataFrame | None:
    """HSI — needs playwright; placeholder."""
    logger.warning("crawl_hsi(%s) — official HSI endpoint not implemented", index_code)
    return None


PROVIDERS = {
    "csi": crawl_csi,
    "szse": crawl_szse,
    "hsi": crawl_hsi,
}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=list(PROVIDERS))
    ap.add_argument("--index", required=True, help="e.g. 000300")
    ap.add_argument("--as-of-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="output xlsx path")
    args = ap.parse_args()

    fn = PROVIDERS[args.provider]
    df = fn(args.index, _date.fromisoformat(args.as_of_date))
    if df is None:
        logger.error("provider %s returned no data for %s", args.provider, args.index)
        sys.exit(1)
    _ok(Path(args.out), df)


if __name__ == "__main__":
    main()