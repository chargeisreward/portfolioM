"""穿透计算引擎

核心算法：权重递归分解
1. 遍历每只基金持仓 → 查跟踪指数 → 获取成分股权重
2. 基金金额 × 成分股权重 = 底层股票持有金额
3. 合并同只股票（多个基金持有 + 直接持股）
4. 归一化为百分比
"""
from datetime import datetime
from sqlalchemy.orm import Session

from models import (
    Holding, Fund, IndexConstituent, StockFinancial,
    PenetrationResult, AssetType
)
from services.growth_bucketer import IndustryChainAnalyzer


class PenetrationEngine:
    """穿透计算引擎"""

    def __init__(self, db: Session):
        self.db = db

    def calculate(self) -> list[PenetrationResult]:
        """执行穿透计算，返回底层股票列表"""
        now = datetime.utcnow()
        stock_map: dict[str, dict] = {}   # stock_code -> {amount, weight, ...}
        total_fund_amount = 0.0
        bond_amount = 0.0
        gold_amount = 0.0

        # ---------- Step 1: 遍历所有持仓 ----------
        holdings = self.db.query(Holding).all()

        for h in holdings:
            atype = h.asset_type

            # 债券和黄金不穿透，单独记账
            if atype == AssetType.BOND.value:
                bond_amount += h.amount
                stock_map.setdefault(f"BOND_{h.security_code}", {
                    "stock_code": h.security_code,
                    "stock_name": h.security_name or "债券类资产",
                    "penetration_amount": h.amount,
                    "asset_category": "bond",
                })
                continue

            if atype == AssetType.GOLD.value:
                gold_amount += h.amount
                stock_map.setdefault(f"GOLD_{h.security_code}", {
                    "stock_code": h.security_code,
                    "stock_name": h.security_name or "黄金类资产",
                    "penetration_amount": h.amount,
                    "asset_category": "gold",
                })
                continue

            # 美股个股和ETF：直接穿透
            if atype in (AssetType.US_STOCK.value, AssetType.US_ETF.value):
                val = h.amount if h.amount > 0 else self._estimate_value(h)
                stock_map.setdefault(h.security_code, {
                    "stock_code": h.security_code,
                    "stock_name": h.security_name or "",
                    "penetration_amount": 0,
                    "asset_category": atype,
                })
                stock_map[h.security_code]["penetration_amount"] += val
                total_fund_amount += val
                continue

            # A股基金/ETF/QDII：通过指数穿透
            if atype in (
                AssetType.A_SHARE_EQUITY.value, AssetType.A_SHARE_ETF.value,
                AssetType.HK_EQUITY.value, AssetType.QDII_EQUITY.value,
            ):
                constituents = self._get_constituents(h.security_code)
                if constituents:
                    total_fund_amount += h.amount
                    for c in constituents:
                        penetrated = h.amount * (c.weight / 100.0)
                        key = c.stock_code or f"UNKNOWN_{c.id}"
                        if key not in stock_map:
                            stock_map[key] = {
                                "stock_code": c.stock_code,
                                "stock_name": c.stock_name,
                                "penetration_amount": 0,
                                "asset_category": atype,
                            }
                        stock_map[key]["penetration_amount"] += penetrated
                else:
                    # ETF但有对应基金信息的：通过基金表找指数
                    fund_info = self.db.query(Fund).filter(
                        Fund.code == h.security_code.replace(".SZ", ".OF").replace(".SH", ".OF")
                    ).first()
                    if fund_info and fund_info.tracking_index_code:
                        constituents = self.db.query(IndexConstituent).filter(
                            IndexConstituent.index_code == fund_info.tracking_index_code
                        ).all()
                        total_fund_amount += h.amount
                        for c in constituents:
                            penetrated = h.amount * (c.weight / 100.0)
                            key = c.stock_code or f"UNKNOWN_{c.id}"
                            if key not in stock_map:
                                stock_map[key] = {
                                    "stock_code": c.stock_code,
                                    "stock_name": c.stock_name,
                                    "penetration_amount": 0,
                                    "asset_category": atype,
                                }
                            stock_map[key]["penetration_amount"] += penetrated

        # ---------- Step 2: 清空旧数据，写入新结果 ----------
        self.db.query(PenetrationResult).delete()

        total_amount = sum(v["penetration_amount"] for v in stock_map.values())
        if total_amount == 0:
            self.db.commit()
            return []

        results = []
        for key, data in stock_map.items():
            weight = (data["penetration_amount"] / total_amount) * 100.0
            result = PenetrationResult(
                stock_code=data["stock_code"],
                stock_name=data["stock_name"],
                penetration_weight=round(weight, 4),
                penetration_amount=round(data["penetration_amount"], 2),
                asset_category=data["asset_category"],
                calculated_at=now,
            )
            self.db.add(result)
            results.append(result)

        # ---------- Step 3: 补充财务数据 ----------
        self._enrich_financials(results)

        self.db.commit()
        return results

    def _get_constituents(self, fund_code: str) -> list[IndexConstituent]:
        """获取基金关联的指数成分股"""
        # Try exact code first
        fund = self.db.query(Fund).filter(Fund.code == fund_code).first()
        if fund and fund.tracking_index_code:
            return self.db.query(IndexConstituent).filter(
                IndexConstituent.index_code == fund.tracking_index_code
            ).all()
        return []

    def _estimate_value(self, h: Holding) -> float:
        """估算美股持仓的当前价值（基于股数，快速接口）"""
        if h.quantity and h.quantity > 0:
            if h.price and h.price > 0:
                return h.quantity * h.price
            from crawlers.price_data import fetch_tencent_quote
            try:
                info = fetch_tencent_quote(h.security_code)
                price = info.get("price") if info else None
                if price:
                    return h.quantity * price
            except Exception:
                pass
        return 0.0

    def _enrich_financials(self, results: list[PenetrationResult]):
        """用财务数据补充穿透结果"""
        for r in results:
            fin = self.db.query(StockFinancial).filter(
                StockFinancial.stock_code == r.stock_code
            ).order_by(StockFinancial.as_of_date.desc()).first()
            if fin:
                r.ttm_pe = fin.ttm_pe
                r.industry_sw = fin.industry_sw
                r.chain_position = fin.chain_position or IndustryChainAnalyzer.classify(fin.industry_sw)
                r.competition = fin.competition
                r.revenue_growth = fin.revenue_growth
                r.profit_growth = fin.profit_growth

                # Calculate forecast PE
                if fin.ttm_pe and fin.profit_growth and fin.profit_growth > 0:
                    r.forecast_pe_1y = round(fin.ttm_pe / (1 + fin.profit_growth / 100.0), 2)
                    r.forecast_pe_2y = round(fin.ttm_pe / (1 + fin.profit_growth / 100.0) ** 2, 2)
