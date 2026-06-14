"""沪深300 数据工具

获取沪深300成分股数据，计算增长阈值、产业链分布、估值基准。
"""
from datetime import date, datetime
from typing import Optional
from sqlalchemy.orm import Session

from models import Csi300Baseline, IndexConstituent, StockFinancial, PenetrationResult
from services.growth_bucketer import IndustryChainAnalyzer
from config import CSI300_CODE, GROWTH_HIGH_THRESHOLD, GROWTH_MED_THRESHOLD


class Csi300Analyzer:
    """沪深300 分析器"""

    def __init__(self, db: Session):
        self.db = db

    def recalc_baselines(self, as_of: date | None = None) -> dict:
        """
        重新计算沪深300所有基准数据。
        从成分股开始，拉取财务数据，计算各维度基准。
        """
        if as_of is None:
            as_of = date.today()
        now = datetime.utcnow()

        # 获取沪深300成分股（已有爬取数据）
        constituents = self.db.query(IndexConstituent).filter(
            IndexConstituent.index_code == CSI300_CODE
        ).all()

        if not constituents:
            return {"error": "CSI300 constituents not found. Run crawler first."}

        results = {}

        # ----- 1. 增长分布 -----
        growth_data = []
        for c in constituents:
            fin = self.db.query(StockFinancial).filter(
                StockFinancial.stock_code == c.stock_code
            ).order_by(StockFinancial.as_of_date.desc()).first()
            if fin and fin.profit_growth is not None:
                growth_data.append({
                    "weight": c.weight,
                    "profit_growth": fin.profit_growth,
                })

        # Sort by profit_growth descending
        growth_data.sort(key=lambda x: x["profit_growth"], reverse=True)

        total_weight = sum(g["weight"] for g in growth_data)
        if total_weight > 0:
            cum_weight = 0.0
            high_cutoff = med_cutoff = None
            for g in growth_data:
                cum_weight += g["weight"]
                if high_cutoff is None and cum_weight / total_weight >= GROWTH_HIGH_THRESHOLD:
                    high_cutoff = g["profit_growth"]
                if med_cutoff is None and cum_weight / total_weight >= GROWTH_MED_THRESHOLD:
                    med_cutoff = g["profit_growth"]
                    break

            self._save_baseline("growth", "high_threshold", high_cutoff, as_of, now)
            self._save_baseline("growth", "med_threshold", med_cutoff, as_of, now)

            # Growth distribution
            dist = {"high": 0.0, "medium": 0.0, "low": 0.0}
            cum = 0.0
            for g in growth_data:
                cum += g["weight"]
                w_pct = g["weight"] / total_weight * 100
                if high_cutoff is not None and g["profit_growth"] >= high_cutoff:
                    dist["high"] += w_pct
                elif med_cutoff is not None and g["profit_growth"] >= med_cutoff:
                    dist["medium"] += w_pct
                else:
                    dist["low"] += w_pct

            for k, v in dist.items():
                self._save_baseline("growth", f"csi300_{k}", round(v, 2), as_of, now)
            results["growth_distribution"] = dist
            results["thresholds"] = {"high_cutoff": high_cutoff, "med_cutoff": med_cutoff}

        # ----- 2. 产业链分布 -----
        chain_dist = {"upstream": 0.0, "midstream": 0.0, "downstream": 0.0,
                      "financial": 0.0, "other": 0.0}
        for c in constituents:
            fin = self.db.query(StockFinancial).filter(
                StockFinancial.stock_code == c.stock_code
            ).order_by(StockFinancial.as_of_date.desc()).first()
            if fin:
                pos = IndustryChainAnalyzer.classify(fin.industry_sw)
                if pos in chain_dist:
                    chain_dist[pos] += c.weight

        total_chain = sum(chain_dist.values())
        if total_chain > 0:
            for k in chain_dist:
                pct = round(chain_dist[k] / total_chain * 100, 2)
                self._save_baseline("industry_chain", f"csi300_{k}", pct, as_of, now)
            results["industry_chain"] = {k: round(v/total_chain*100, 2)
                                          for k, v in chain_dist.items() if v > 0}

        # ----- 3. 估值基准 -----
        pe_values = []
        total_pe_weight = 0.0
        for c in constituents:
            fin = self.db.query(StockFinancial).filter(
                StockFinancial.stock_code == c.stock_code
            ).order_by(StockFinancial.as_of_date.desc()).first()
            if fin and fin.ttm_pe and fin.ttm_pe > 0 and fin.ttm_pe < 500:
                pe_values.append((c.weight, fin.ttm_pe))
                total_pe_weight += c.weight

        if pe_values:
            weighted_pe = sum(w * pe for w, pe in pe_values) / total_pe_weight
            self._save_baseline("valuation", "csi300_weighted_pe", round(weighted_pe, 2), as_of, now)
            results["weighted_pe"] = round(weighted_pe, 2)

        self.db.commit()
        return results

    def _save_baseline(self, dimension: str, category: str,
                       value: Optional[float], as_of: date, now: datetime):
        """Save or update a baseline metric"""
        existing = self.db.query(Csi300Baseline).filter(
            Csi300Baseline.dimension == dimension,
            Csi300Baseline.category == category,
        ).first()

        if existing:
            existing.value = value
            existing.as_of_date = as_of
            existing.created_at = now
        else:
            bl = Csi300Baseline(
                dimension=dimension,
                category=category,
                value=value,
                as_of_date=as_of,
                created_at=now,
            )
            self.db.add(bl)

    def get_baselines(self) -> dict:
        """获取所有已保存的基准数据"""
        rows = self.db.query(Csi300Baseline).all()
        result = {}
        for r in rows:
            if r.dimension not in result:
                result[r.dimension] = {}
            result[r.dimension][r.category] = r.value
        return result
