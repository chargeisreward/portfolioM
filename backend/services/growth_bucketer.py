"""增长分层器

算法：沪深300 加权分位法
1. 对沪深300成分股按利润增速从高到低排序
2. 累加权重至33% → 高增长阈值
3. 累加至66% → 中增长阈值
4. 用同样的阈值切割组合持仓的底层股票
"""
from typing import Optional
from sqlalchemy.orm import Session
from models import PenetrationResult, Csi300Baseline, GrowthTier
from config import GROWTH_HIGH_THRESHOLD, GROWTH_MED_THRESHOLD


class GrowthBucketer:
    """增长分层器"""

    def __init__(self, db: Session):
        self.db = db

    def calculate_csi300_thresholds(self) -> dict[str, Optional[float]]:
        """
        基于沪深300成分股计算增长阈值。
        返回 {high_cutoff, med_cutoff} — 即高/中、中/低的分界利润增速值。
        """
        baselines = self.db.query(Csi300Baseline).filter(
            Csi300Baseline.dimension == "growth"
        ).all()

        high_cutoff = med_cutoff = None
        for b in baselines:
            if b.category == "high_threshold":
                high_cutoff = b.value
            elif b.category == "med_threshold":
                med_cutoff = b.value

        return {"high_cutoff": high_cutoff, "med_cutoff": med_cutoff}

    def classify(self, profit_growth: Optional[float], thresholds: dict) -> str:
        """根据阈值判断增长层级"""
        if profit_growth is None:
            return GrowthTier.UNKNOWN.value

        high = thresholds.get("high_cutoff")
        med = thresholds.get("med_cutoff")

        if high is not None and profit_growth >= high:
            return GrowthTier.HIGH.value
        if med is not None and profit_growth >= med:
            return GrowthTier.MEDIUM.value
        return GrowthTier.LOW.value

    def compute_portfolio_growth_distribution(
        self, thresholds: dict, user_id: int | None = None
    ) -> dict[str, float]:
        """计算组合的增长层级分布（user_id=None 表示全部 — 仅供 admin/共享主数据场景；常规调用须传 user_id）"""
        q = self.db.query(PenetrationResult)
        if user_id is not None:
            q = q.filter(PenetrationResult.user_id == user_id)
        results = q.all()

        distribution = {"high": 0.0, "medium": 0.0, "low": 0.0, "unknown": 0.0}

        for r in results:
            tier = self.classify(r.profit_growth, thresholds)
            distribution[tier] += r.penetration_weight

        # Normalize to 100%
        total = sum(distribution.values())
        if total > 0:
            for k in distribution:
                distribution[k] = round(distribution[k] / total * 100, 2)

        return distribution


class IndustryChainAnalyzer:
    """产业链位置分析"""

    # 申万行业 → 产业链位置映射表
    # 基于东方财富/申万行业分类
    INDUSTRY_MAP = {
        # 上游（原材料/资源）
        "有色金属": "upstream", "钢铁": "upstream", "煤炭": "upstream",
        "石油石化": "upstream", "基础化工": "upstream", "采掘": "upstream",
        "农林牧渔": "upstream", "石油": "upstream", "化工": "upstream",
        "材料": "upstream", "原材料": "upstream",

        # 中游（制造/加工）
        "电力设备": "midstream", "机械设备": "midstream", "电子": "midstream",
        "半导体": "midstream", "电气设备": "midstream", "汽车": "midstream",
        "国防军工": "midstream", "军工": "midstream", "建筑装饰": "midstream",
        "建筑材料": "midstream", "交通运输": "midstream", "建筑": "midstream",
        "制造业": "midstream",

        # 下游（消费/服务）
        "食品饮料": "downstream", "医药生物": "downstream", "医药": "downstream",
        "家用电器": "downstream", "商贸零售": "downstream", "商业贸易": "downstream",
        "休闲服务": "downstream", "传媒": "downstream", "计算机": "downstream",
        "通信": "downstream", "房地产": "downstream", "社会服务": "downstream",
        "轻工制造": "downstream", "纺织服饰": "downstream", "美容护理": "downstream",
        "环保": "downstream", "公用事业": "downstream",

        # 金融（单独分类）
        "银行": "financial", "非银金融": "financial", "券商": "financial",
        "保险": "financial", "多元金融": "financial",
    }

    @classmethod
    def classify(cls, industry: str | None) -> str:
        """根据行业名称判断产业链位置"""
        if not industry:
            return "other"

        industry = industry.strip()
        # Try exact match first
        if industry in cls.INDUSTRY_MAP:
            return cls.INDUSTRY_MAP[industry]

        # Try partial match
        for keyword, position in cls.INDUSTRY_MAP.items():
            if keyword in industry or industry in keyword:
                return position

        return "other"

    @classmethod
    def compute_distribution(cls, results: list[PenetrationResult]) -> dict[str, float]:
        """计算组合的产业链分布"""
        dist = {"upstream": 0.0, "midstream": 0.0, "downstream": 0.0,
                "financial": 0.0, "other": 0.0, "bond": 0.0, "gold": 0.0}

        for r in results:
            if r.asset_category in ("bond", "gold"):
                dist[r.asset_category] += r.penetration_weight
            elif r.chain_position:
                pos = r.chain_position
                if pos in dist:
                    dist[pos] += r.penetration_weight
                else:
                    pos = cls.classify(r.industry_sw)
                    dist[pos] += r.penetration_weight
            else:
                pos = cls.classify(r.industry_sw)
                dist[pos] += r.penetration_weight

        return {k: round(v, 2) for k, v in dist.items() if v > 0}
