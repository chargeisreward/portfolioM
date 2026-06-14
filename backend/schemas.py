"""Pydantic response/request schemas"""
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class HoldingOut(BaseModel):
    id: int
    security_code: str
    security_name: Optional[str] = None
    quantity: float = 0
    price: Optional[float] = None
    amount: float = 0
    asset_type: Optional[str] = None
    class Config:
        from_attributes = True


class HoldingSummary(BaseModel):
    total_value: float
    categories: dict[str, float]  # asset_type -> amount
    fund_count: int
    stock_count: int


class PenetrationRow(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    penetration_weight: float
    penetration_amount: float
    industry_sw: Optional[str] = None
    chain_position: Optional[str] = None
    growth_tier: Optional[str] = None
    competition: Optional[str] = None
    ttm_pe: Optional[float] = None
    forecast_pe_1y: Optional[float] = None
    forecast_pe_2y: Optional[float] = None
    revenue_growth: Optional[float] = None
    profit_growth: Optional[float] = None


class PenetrationSummary(BaseModel):
    total_penetrated: float        # 成功穿透的比例 %
    stock_count: int                # 底层股票数
    top_holdings: list[PenetrationRow]


class IndustryChainAnalysis(BaseModel):
    portfolio: dict[str, float]       # upstream/midstream/downstream -> weight%
    csi300: Optional[dict[str, float]] = None


class GrowthAnalysis(BaseModel):
    thresholds: dict[str, Optional[float]]  # high_cutoff, med_cutoff
    portfolio: dict[str, float]              # high/medium/low -> weight%
    csi300: Optional[dict[str, float]] = None


class ValuationMetrics(BaseModel):
    portfolio_weighted_pe: Optional[float] = None
    portfolio_forecast_pe_1y: Optional[float] = None
    portfolio_forecast_pe_2y: Optional[float] = None
    csi300_pe: Optional[float] = None


class PricePoint(BaseModel):
    date: str
    close: float


class PriceSeries(BaseModel):
    code: str
    name: Optional[str] = None
    prices: list[PricePoint]


class ImportRequest(BaseModel):
    file_path: Optional[str] = None  # for file upload


class CrawlResponse(BaseModel):
    status: str
    message: str
    count: Optional[int] = None


class SecurityMasterOut(BaseModel):
    """证券基础表输出"""
    security_code: str
    security_name: Optional[str] = None
    currency: str = "CNY"
    asset_type: Optional[str] = None
    type2: Optional[str] = None
    exchange: Optional[str] = None


class SecurityMasterUpsert(BaseModel):
    """证券基础表新增/更新"""
    security_code: str
    security_name: Optional[str] = None
    currency: str = "CNY"
    asset_type: Optional[str] = None
    type2: Optional[str] = None
    exchange: Optional[str] = None


class SecurityTypeConfigOut(BaseModel):
    """证券类型配置输出"""
    asset_type: str
    type_name: Optional[str] = None
    price_precision: int = 2
    amount_precision: int = 0
    sort_order: int = 0


class SecurityTypeConfigUpsert(BaseModel):
    """证券类型配置新增/更新"""
    type_name: Optional[str] = None
    price_precision: int = 2
    amount_precision: int = 0
    sort_order: int = 0
