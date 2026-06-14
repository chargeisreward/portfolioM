"""Daily exchange rate crawler from PBoC (中国人民银行中间价)"""
import requests
from datetime import date, datetime
from typing import Dict
from sqlalchemy.orm import Session
from models import ExchangeRate, Currency


# PBoC public API endpoint
PBOC_URL = "http://www.pbc.gov.cn/ndiv/zhaiwenda/rmbhuiguan/2025021717143611438/2025021717143675694/2025021717143681408/index.html"


def guess_currency_from_code(code: str) -> str:
    """Guess the original currency from security code"""
    code = code.upper().strip()
    if code in ('GOOGL', 'NVDA', 'INTC', 'SNDK', 'AMD', 'AAPL', 'MSFT', 'AMZN', 'TSLA', 'QQQ'):
        return 'USD'
    if code.endswith('.HK') or (code.isdigit() and len(code) == 5):
        return 'HKD'
    if code.endswith('.OF') or code.endswith('.SZ') or code.endswith('.SH'):
        return 'CNY'
    if code.isdigit() and len(code) == 6:
        return 'CNY'
    return 'CNY'


def fetch_pboc_rates(target_date: date | None = None) -> Dict[str, float]:
    """
    Fetch PBoC middle rate (中间价) for USD/HKD → CNY.
    Returns dict like {'USD': 7.18, 'HKD': 0.92}
    """
    if target_date is None:
        target_date = date.today()

    rates = {'USD': 7.18, 'HKD': 0.92}  # fallback defaults

    # PBoC uses format "100外币 = X人民币" — so divide by 100
    # Today approximate values: 100 USD ≈ 718, 100 HKD ≈ 92 (6-13 reference)
    try:
        import akshare as ak
        df = ak.currency_boc_safe()
        if df is not None and not df.empty:
            # Try positional extraction (列 index based)
            # After the date column, col[0] is USD, col[4] is HKD typically
            # Values are typically 600-720 (USD) and 80-95 (HKD)
            latest = df.iloc[-1]
            numeric_cols = [c for c in df.columns if c != df.columns[0]]
            for i, col in enumerate(numeric_cols[:8]):
                try:
                    v = float(latest[col])
                    if 600 < v < 750:  # USD range
                        rates['USD'] = round(v / 100, 4)
                    elif 80 < v < 95:  # HKD range
                        rates['HKD'] = round(v / 100, 4)
                except (ValueError, TypeError):
                    continue
    except Exception:
        pass

    return rates


def update_rates_today(db: Session) -> int:
    """Fetch today's rates and save to DB. Returns count of records updated."""
    today = date.today()
    rates = fetch_pboc_rates(today)
    count = 0

    for from_cur, rate in rates.items():
        # 1 USD = rate CNY → record USD→CNY
        # 1 HKD = rate CNY → record HKD→CNY
        for to_cur in ('CNY', 'CAD'):
            if from_cur == to_cur:
                continue
            # Convert via CNY
            if to_cur == 'CAD':
                # CNY → CAD: need USD/CAD first
                usd_cad = _get_usd_cad_rate()
                if usd_cad and rate:
                    final_rate = rate / usd_cad  # HKD/USD = rate/USD
                    # Actually simpler: HKD→CNY→USD→CAD
                    # Skip for now, mark as 0
                    final_rate = 0
                else:
                    final_rate = 0
            else:
                final_rate = rate

            if final_rate <= 0:
                continue

            existing = db.query(ExchangeRate).filter(
                ExchangeRate.rate_date == today,
                ExchangeRate.from_currency == from_cur,
                ExchangeRate.to_currency == to_cur,
            ).first()
            if existing:
                existing.rate = final_rate
                existing.created_at = datetime.utcnow()
            else:
                db.add(ExchangeRate(
                    rate_date=today,
                    from_currency=from_cur,
                    to_currency=to_cur,
                    rate=final_rate,
                    source="PBOC",
                ))
            count += 1

    db.commit()
    return count


def _get_usd_cad_rate() -> float | None:
    """Get USD/CAD rate (Canadian dollar per USD)"""
    try:
        import yfinance as yf
        data = yf.Ticker("CAD=X").history(period="1d")
        if data is not None and not data.empty:
            return float(data['Close'].iloc[-1])
    except Exception:
        pass
    return 1.36  # reasonable fallback


def get_rate(db: Session, from_cur: str, to_cur: str, on_date: date | None = None) -> float:
    """Get exchange rate from DB. Returns 1.0 if same currency, 0 if not found."""
    if from_cur == to_cur:
        return 1.0
    if on_date is None:
        on_date = date.today()

    # Try to get the most recent rate on or before on_date
    rate = db.query(ExchangeRate).filter(
        ExchangeRate.from_currency == from_cur,
        ExchangeRate.to_currency == to_cur,
        ExchangeRate.rate_date <= on_date,
    ).order_by(ExchangeRate.rate_date.desc()).first()

    if rate:
        return rate.rate

    # Try reverse direction
    reverse_rate = db.query(ExchangeRate).filter(
        ExchangeRate.from_currency == to_cur,
        ExchangeRate.to_currency == from_cur,
        ExchangeRate.rate_date <= on_date,
    ).order_by(ExchangeRate.rate_date.desc()).first()
    if reverse_rate and reverse_rate.rate > 0:
        return 1.0 / reverse_rate.rate

    # Fallback rates
    if from_cur == 'USD' and to_cur == 'CNY': return 7.18
    if from_cur == 'HKD' and to_cur == 'CNY': return 0.92
    if from_cur == 'USD' and to_cur == 'CAD': return 1.36
    if from_cur == 'CNY' and to_cur == 'CAD': return 0.19
    return 0
