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
    cash_cny: float = 0.0  # 现金（来自 HoldingDailySnapshot 最新日 CASH 行）


class PenetrationRow(BaseModel):
    model_config = {"from_attributes": True, "extra": "ignore"}
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
    revenue_growth: Optional[str] = None
    profit_growth: Optional[str] = None


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
    exchange: Optional[str] = None
    is_drillable: bool = False


class SecurityMasterUpsert(BaseModel):
    """证券基础表新增/更新"""
    security_code: str
    security_name: Optional[str] = None
    currency: str = "CNY"
    asset_type: Optional[str] = None
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


# ---------- 交易记录驱动的持仓重建 (2026-06-26) ----------

class TradeParseRequest(BaseModel):
    """交易记录解析请求"""
    text: str


class TradeConfirmItem(BaseModel):
    """单笔交易确认项（用户可编辑）"""
    trade_date: date
    security_code: str
    security_name: Optional[str] = None
    trade_type: str  # buy/sell/dividend/others
    confirmed_shares: float = 0.0
    confirmed_amount: float = 0.0
    nav_price: Optional[float] = None
    nav_date: Optional[date] = None
    fee: Optional[float] = None
    remarks: Optional[str] = None


class TradeConfirmRequest(BaseModel):
    """交易确认提交请求"""
    trades: list[TradeConfirmItem]


class TradeUpdateRequest(BaseModel):
    """单条历史交易更新请求（可编辑字段）"""
    trade_date: date
    security_code: str
    security_name: Optional[str] = None
    trade_type: str  # buy/sell/dividend/others
    confirmed_shares: float = 0.0
    confirmed_amount: float = 0.0
    nav_price: Optional[float] = None
    nav_date: Optional[date] = None
    fee: Optional[float] = None
    remarks: Optional[str] = None


class ParsedTradeItem(TradeConfirmItem):
    """解析结果项（含证券状态）"""
    security_status: str = "exists"  # exists / new_verified / new_unverified / failed
    security_message: Optional[str] = None


class TradeParseResponse(BaseModel):
    """交易记录解析响应"""
    trades: list[ParsedTradeItem]
    parse_error: Optional[str] = None


class TradeOut(BaseModel):
    """交易记录输出"""
    id: int
    trade_date: date
    security_code: str
    security_name: Optional[str] = None
    trade_type: str
    confirmed_shares: float
    confirmed_amount: float
    nav_price: Optional[float] = None
    nav_date: Optional[date] = None
    fee: Optional[float] = None
    remarks: Optional[str] = None
    security_verified: bool
    security_added_to_master: bool
    class Config:
        from_attributes = True


class HoldingSnapshotOut(BaseModel):
    """持仓快照行输出（含 CASH 行）"""
    security_code: str
    security_name: Optional[str] = None
    quantity: float
    price: Optional[float] = None
    price_cny: Optional[float] = None
    currency: str
    amount_cny: float
    asset_type: Optional[str] = None
    is_cash: bool
    class Config:
        from_attributes = True


class TradingSessionOut(BaseModel):
    """交易会话输出"""
    start_date: date
    initial_cash: float
    initial_snapshot_built: bool
    last_rebuild_date: Optional[date] = None
    class Config:
        from_attributes = True


class SnapshotRangeOut(BaseModel):
    """快照日期范围输出"""
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class TradeConfirmResponse(BaseModel):
    """交易确认提交响应"""
    confirmed_count: int
    latest_snapshot: list[HoldingSnapshotOut]
