"""Import portfolio holdings from Excel"""
from datetime import datetime, date
from openpyxl import load_workbook
from sqlalchemy.orm import Session
from models import Holding, AssetType, Currency
from crawlers.exchange_rates import guess_currency_from_code, get_rate


def guess_asset_type(code: str) -> str:
    """Guess asset type from security code"""
    code = str(code).strip().upper()

    if code in ('GOOGL', 'NVDA', 'INTC', 'SNDK', 'AMD', 'AAPL', 'MSFT', 'AMZN', 'TSLA'):
        return AssetType.US_STOCK.value
    if code == 'QQQ':
        return AssetType.US_ETF.value

    if code.endswith('.SZ') or code.endswith('.SH'):
        return AssetType.A_SHARE_ETF.value

    if code.endswith('.OF'):
        if code.startswith(('006829', '014856', '006517')):
            return AssetType.BOND.value
        if code.startswith(('008701', '008702', '002611')):
            return AssetType.GOLD.value
        if code.startswith(('019524', '019525', '006479', '015311', '007722')):
            return AssetType.QDII_EQUITY.value
        if code.startswith(('018388', '021142')):
            return AssetType.HK_EQUITY.value
        return AssetType.A_SHARE_EQUITY.value

    return AssetType.CASH.value


def import_excel(filepath: str, db: Session, batch: str | None = None) -> int:
    """
    Import holdings from Excel, deduplicating same code+quantity rows.
    Fund 'amount' is stored as quantity (份额).
    Adds price and calculates amount = quantity × price.
    """
    if batch is None:
        batch = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    # Delete old holdings for this import
    db.query(Holding).delete()

    wb = load_workbook(filepath, data_only=True)
    ws = wb[wb.sheetnames[0]]

    # Dedup map: (code, quantity) → merged record
    dedup: dict[tuple[str, float], dict] = {}

    for row in ws.iter_rows(min_row=3, max_row=ws.max_row, values_only=True):
        code, name, col2, col3 = row[0], row[1], row[2], row[3]
        if code is None:
            continue

        code = str(code).strip()
        name = str(name).strip() if name else ""
        asset_type = guess_asset_type(code)

        # Determine quantity: all fund amounts ARE quantities (份额)
        if asset_type in (AssetType.US_STOCK.value, AssetType.US_ETF.value):
            # US stocks: column 3 = share count
            quantity = float(col2) if col2 else 0.0
        else:
            # Chinese funds: column 3 = 份额 (quantity, not monetary amount)
            # column 4 is duplicate of column 3
            quantity = float(col3) if col3 else (float(col2) if col2 else 0.0)

        if quantity == 0:
            continue

        key = (code, round(quantity, 4))
        if key in dedup:
            continue  # skip duplicate

        dedup[key] = {
            "security_code": code,
            "security_name": name,
            "quantity": quantity,
            "price": None,
            "currency": guess_currency_from_code(code),
            "amount": 0.0,
            "amount_cny": 0.0,
            "asset_type": asset_type,
            "import_batch": batch,
        }

    # Write deduplicated records
    count = 0
    for data in dedup.values():
        h = Holding(**data)
        db.add(h)
        count += 1

    db.commit()
    wb.close()
    return count


def _fetch_fund_nav(fund_code: str) -> float | None:
    """Get latest NAV for a Chinese fund via akshare.
    Tries 单位净值 first, then fallback to 累计净值 for QDII/bond funds."""
    try:
        import akshare as ak
        # Try 单位净值走势 (standard)
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is not None and len(df) > 0:
            nav = df.iloc[-1].get("单位净值")
            if nav is not None:
                return float(nav)
    except Exception:
        pass

    try:
        # Fallback: 累计净值走势 (QDII, some bond funds)
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="累计净值走势")
        if df is not None and len(df) > 0:
            nav = df.iloc[-1].get("累计净值")
            if nav is not None:
                return float(nav)
    except Exception:
        pass
    return None


def fetch_fund_nav_history(fund_code: str, days: int = 90) -> list[dict]:
    """拉 OF 基金过去 N 天每日净值（真实数据，来自东方财富）。
    返回 [{date: 'YYYY-MM-DD', close: 1.234}, ...]
    优先用「单位净值走势」，单位净值为空的基金（如某些 QDII/债券）用「累计净值走势」。"""
    from datetime import timedelta

    def _try(indicator: str, value_col: str) -> list[dict]:
        try:
            import akshare as ak
            df = ak.fund_open_fund_info_em(symbol=fund_code, indicator=indicator)
        except Exception:
            return []
        if df is None or df.empty:
            return []
        out = []
        cutoff = date.today() - timedelta(days=days)
        for _, row in df.iterrows():
            try:
                raw_date = row.get("净值日期")
                if raw_date is None:
                    raw_date = row.get("日期")
                if raw_date is None:
                    continue
                # 统一成 date 对象
                # pandas.Timestamp → date
                # datetime.datetime → date
                # datetime.date → 直接用
                # 字符串 → 解析
                if hasattr(raw_date, "date") and callable(raw_date.date) and not isinstance(raw_date, date):
                    d = raw_date.date()
                elif isinstance(raw_date, date):
                    d = raw_date
                else:
                    d = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
                if d < cutoff:
                    continue
                nav = row.get(value_col)
                if nav is None or str(nav) == "nan":
                    continue
                out.append({"date": d.isoformat(), "close": float(nav)})
            except Exception:
                # 单行解析失败不影响整体
                continue
        return out

    result = _try("单位净值走势", "单位净值")
    if result:
        return result
    return _try("累计净值走势", "累计净值")


def fill_prices(db: Session):
    """Fetch latest prices for all holdings and calculate amount = quantity × price.
    US stocks → Tencent API | Chinese funds → akshare NAV | ETFs → Tencent API"""
    from crawlers.price_data import fetch_tencent_quote
    holdings = db.query(Holding).all()
    updated = 0

    for h in holdings:
        try:
            price = None
            code = h.security_code

            # US stocks/ETFs via Tencent
            if h.asset_type in (AssetType.US_STOCK.value, AssetType.US_ETF.value):
                info = fetch_tencent_quote(code)
                if info:
                    price = info.get("price")

            # Chinese OTC funds via akshare NAV
            if not price and code.endswith(".OF"):
                fund_code = code.replace(".OF", "")
                nav = _fetch_fund_nav(fund_code)
                if nav and nav > 0:
                    price = nav

            # Chinese exchange-traded ETFs via Tencent
            if not price and (code.endswith(".SZ") or code.endswith(".SH") or code.endswith(".OF")):
                info = fetch_tencent_quote(code)
                if info:
                    price = info.get("price")

            if price and price > 0:
                h.price = round(price, 4)
                h.amount = round(h.quantity * price, 2)
                # Convert to CNY
                rate = get_rate(db, h.currency, 'CNY')
                if rate > 0:
                    h.amount_cny = round(h.amount * rate, 2)
                else:
                    h.amount_cny = h.amount
                updated += 1
        except Exception:
            pass

    # For holdings still without price: amount = quantity (unit price ≈ 1)
    for h in holdings:
        if (h.amount is None or h.amount == 0) and h.quantity > 0:
            h.price = h.price or 1.0
            h.amount = round(h.quantity * h.price, 2)

    db.commit()
    return updated


def get_holdings_summary(db: Session) -> dict:
    """Get portfolio summary by asset category"""
    from sqlalchemy import func
    rows = db.query(
        Holding.asset_type,
        func.sum(Holding.amount).label("total")
    ).group_by(Holding.asset_type).all()

    categories = {}
    total = 0.0
    for r in rows:
        v = float(r.total or 0)
        categories[r.asset_type] = v
        total += v

    fund_count = db.query(Holding).filter(
        Holding.asset_type.in_([
            AssetType.A_SHARE_EQUITY.value, AssetType.A_SHARE_ETF.value,
            AssetType.HK_EQUITY.value, AssetType.QDII_EQUITY.value,
            AssetType.US_ETF.value,
        ])
    ).count()

    stock_count = db.query(Holding).filter(
        Holding.asset_type == AssetType.US_STOCK.value
    ).count()

    return {
        "total_value": round(total, 2),
        "categories": {k: round(v, 2) for k, v in categories.items()},
        "fund_count": fund_count,
        "stock_count": stock_count,
    }
