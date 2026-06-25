"""下钻编排 service — 唯一耦合点。
调 public service + user service，join 后返回完整结果。

join 公式：
  user_drill_shares = user_quantity × fund.shares_equivalent
  user_hold_shares = total_drill_shares × constituent.weight
  user_hold_value = user_hold_shares × constituent.current_price

用户层金额字段（2026-06-25 补全）：
  static_amount_cny  = Σ (user_quantity × per_fund_static[f])    # 用户实际基准日金额
  est_market_value_cny = card_est                                # 成分股估算市值
  est_deviation_pct  = ((card_est + card_cash) / card_fund_value - 1) × 100
  weight_pct         = card_est / user_total_est × 100           # 卡片占组合估算总市值比重
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy.orm import Session

from models import FundDrillSnapshot
from services import drill_public_service as public_service
from services import drill_user_service as user_service

logger = logging.getLogger(__name__)


def list_drillable_cards(db: Session, as_of: _date, user_id: int) -> list[dict]:
    """返回用户可见的下钻卡片列表。

    join 逻辑：
    1. public.get_public_cards(as_of) → 所有公共卡片
    2. user.get_user_fund_codes(user_id) → 用户基金代码集合
    3. if not user_fund_codes → return []
    4. 过滤：只保留 fund_codes ∩ user_fund_codes 非空的卡片
    5. 计算 est_market_value_cny / static_amount_cny / est_deviation_pct / weight_pct
    """
    public_cards = public_service.get_public_cards(db, as_of)
    user_fund_codes = user_service.get_user_fund_codes(db, user_id)

    if not user_fund_codes:
        return []

    if not public_cards:
        return []

    # 获取用户持仓明细（用于计算 card_fund_value = 基金份额 × 基金净值）
    user_holdings = user_service.get_user_fund_holdings(
        db, user_id, list(user_fund_codes)
    )

    # 拉所有 public_card 涉及 fund 的 drill snapshot 行
    # 按 fund_code 累加 per_fund_static (Σ shares_eq×baseline) 和 per_fund_est (Σ shares_eq×current)
    all_fund_codes: set[str] = set()
    for card in public_cards:
        all_fund_codes.update(card["fund_codes"])
    # 仅查用户持有的 fund
    relevant_fund_codes = all_fund_codes & user_fund_codes

    per_fund_static: dict[str, float] = {}   # fund_code → Σ shares_eq × baseline_price_cny (CNY)
    per_fund_est: dict[str, float] = {}      # fund_code → Σ shares_eq × current_price_cny (CNY)
    if relevant_fund_codes:
        from sqlalchemy import func as sa_func
        # public_cards[0]["as_of"] 是 ISO 字符串（来自 effective_date.isoformat()）
        effective_date = _date.fromisoformat(public_cards[0]["as_of"])
        # 双币种规则 (2026-06-25)：聚合用本币(CNY)字段，保证 A 股/H 股量纲一致。
        # 本币字段在公共数据层算好存入表，此处直接取 baseline_price_cny / current_price_cny。
        rows = db.query(
            FundDrillSnapshot.fund_code,
            sa_func.sum(FundDrillSnapshot.shares_equivalent * FundDrillSnapshot.baseline_price_cny).label("static_sum"),
            sa_func.sum(FundDrillSnapshot.shares_equivalent * FundDrillSnapshot.current_price_cny).label("est_sum"),
        ).filter(
            FundDrillSnapshot.as_of_date == effective_date,
            FundDrillSnapshot.fund_code.in_(list(relevant_fund_codes)),
        ).group_by(FundDrillSnapshot.fund_code).all()
        for r in rows:
            per_fund_static[r.fund_code] = float(r.static_sum or 0.0)
            per_fund_est[r.fund_code] = float(r.est_sum or 0.0)

    # 先计算每张卡片的 card_est（用于稍后算 weight_pct = card_est / user_total_est）
    cards_with_est: list[dict] = []
    user_total_est = 0.0
    for card in public_cards:
        overlap = set(card["fund_codes"]) & user_fund_codes
        if not overlap:
            continue
        # card_est = Σ (user_quantity × per_fund_est[f]) for f in overlap（成分股+现金估算市值）
        # card_fund_value = Σ (user_holdings[f].amount_cny) for f in overlap（基金份额 × 基金净值）
        # card_static = Σ (user_quantity × per_fund_static[f]) for f in overlap（用户实际基准日金额）
        # 注意：per_fund_est / per_fund_static 已包含 CASH 行（来自 FundDrillSnapshot），
        # 故 card_est 自动包含 5% 现金部分，无需额外加 card_cash。
        card_est = 0.0
        card_fund_value = 0.0
        card_static = 0.0
        for f in overlap:
            h = user_holdings.get(f)
            if not h:
                continue
            quantity = h["quantity"]
            card_est += quantity * per_fund_est.get(f, 0.0)
            card_fund_value += h["amount_cny"]
            card_static += quantity * per_fund_static.get(f, 0.0)

        # est_deviation_pct = (估算市值 / 基金市值 - 1) × 100
        # card_est 已含 5% 现金，理论上 ≈ card_fund_value → deviation ≈ 0
        if card_fund_value > 0:
            est_deviation_pct = round(
                (card_est / card_fund_value - 1) * 100, 4
            )
        else:
            est_deviation_pct = None

        merged = {
            **card,
            "user_fund_codes": sorted(overlap),
            "static_amount_cny": round(card_static, 4),       # 覆盖公共层值（用户实际金额）
            "est_market_value_cny": round(card_est, 4),       # 估算市值
            "est_deviation_pct": est_deviation_pct,
            "card_fund_value_cny": round(card_fund_value, 4), # 基金市值（debug 用）
        }
        cards_with_est.append(merged)
        user_total_est += card_est

    # 计算 weight_pct 并组装最终结果
    result = []
    for merged in cards_with_est:
        card_est = merged["est_market_value_cny"]
        weight_pct = round(card_est / user_total_est * 100, 4) if user_total_est > 0 else 0.0
        merged["weight_pct"] = weight_pct
        result.append(merged)

    # 卡片按 weighted_pe 从高到低排序（无 PE 数据的卡片排末尾）
    # 前端 grid 布局：数组顺序 = 从左到右、从上到下的视觉顺序
    result.sort(key=lambda c: (c.get("weighted_pe") is None, -(c.get("weighted_pe") or 0)))
    return result


def get_drill_detail(
    db: Session, as_of: _date, index_code: str, user_id: int
) -> dict | None:
    """返回用户可见的下钻明细。

    join 逻辑：
    1. public.get_public_detail(as_of, index_code) → 公共明细
    2. if not public_detail → return None
    3. user.get_user_fund_holdings(user_id, fund_codes) → 用户持仓
    4. if not user_holdings → return None
    5. 直接查 FundDrillSnapshot 获取 per-fund × per-stock 的 shares_equivalent
    6. join：user_hold_shares = Σ(user_quantity[f] × shares_eq[f][s])
    7. user_hold_value = user_hold_shares × current_price
    """
    public_detail = public_service.get_public_detail(db, as_of, index_code)
    if not public_detail:
        return None

    fund_codes = [f["fund_code"] for f in public_detail.get("funds", [])]
    user_holdings = user_service.get_user_fund_holdings(db, user_id, fund_codes)

    if not user_holdings:
        return None

    # 直接查 FundDrillSnapshot 获取 per-fund × per-stock 的 shares_equivalent
    # 用于精确计算 user_hold_shares = Σ(user_quantity[f] × shares_eq[f][s])
    effective_date = _date.fromisoformat(public_detail["as_of"])
    # 双币种规则 (2026-06-25)：取本币(CNY)价 current_price_cny 算市值，保证量纲一致。
    # shares_equivalent 已用 CNY 价算，× current_price_cny = CNY 市值。
    drill_rows = db.query(
        FundDrillSnapshot.fund_code,
        FundDrillSnapshot.stock_code,
        FundDrillSnapshot.shares_equivalent,
        FundDrillSnapshot.current_price_cny,
    ).filter(
        FundDrillSnapshot.as_of_date == effective_date,
        FundDrillSnapshot.fund_code.in_(list(user_holdings.keys())),
    ).all()

    # 构建 stock_code → {user_hold_shares, user_hold_value} 映射
    # user_hold_shares = Σ(user_quantity[f] × shares_eq[f][s])  对每只持有的 fund
    # user_hold_value  = user_hold_shares × current_price_cny（本币 CNY 市值）
    stock_user_shares: dict[str, float] = {}
    stock_user_value: dict[str, float] = {}
    for dr in drill_rows:
        h = user_holdings.get(dr.fund_code)
        if not h:
            continue
        user_qty = h["quantity"]
        shares_eq = dr.shares_equivalent or 0.0
        # 用户约当股数 = 持有份额 × 每份基金含股票约当数值
        user_hold_shares = user_qty * shares_eq
        stock_user_shares[dr.stock_code] = stock_user_shares.get(dr.stock_code, 0.0) + user_hold_shares
        # 估算市值 = 约当股数 × 本币当前价 (CNY)
        price = dr.current_price_cny or 0.0
        stock_user_value[dr.stock_code] = stock_user_value.get(dr.stock_code, 0.0) + user_hold_shares * price

    # join：计算每只基金的 user_drill_shares（用于 funds 列表展示）
    funds_joined = []
    total_user_drill_shares = 0.0
    for f in public_detail["funds"]:
        h = user_holdings.get(f["fund_code"])
        if not h:
            continue
        # fund 级别的 user_drill_shares = user_quantity × Σ(shares_eq[该fund所有股票])
        # 注意：f["shares_equivalent"] 是公共层聚合值（所有成分股 shares_eq 之和），
        # 这里用于展示 fund 级别的"用户总约当股数"，精确的 per-stock 值在 constituents 中
        user_drill_shares = h["quantity"] * (f.get("shares_equivalent") or 0.0)
        funds_joined.append({
            **f,
            "user_quantity": h["quantity"],
            "user_drill_shares": round(user_drill_shares, 4),
        })
        total_user_drill_shares += user_drill_shares

    # join：constituents 使用精确的 per-stock user_hold_shares
    # 注意：CASH 行已由公共层 get_public_detail 返回（来自 FundDrillSnapshot 的 CASH 行），
    # drill_rows 查询自动包含 CASH 行，stock_user_shares/stock_user_value 已含 CASH 值，
    # 故此处无需额外追加 CASH 行 — 它像普通成分股一样流过 join。
    constituents_joined = []
    for c in public_detail["constituents"]:
        stock_code = c["stock_code"]
        # 用户约当股数（精确值，来自 per-fund × per-stock 明细查询）
        user_hold_shares = stock_user_shares.get(stock_code, 0.0)
        # 估算市值（精确值，来自 per-fund × per-stock 明细累加）
        user_hold_value = stock_user_value.get(stock_code, 0.0)
        constituents_joined.append({
            **c,
            # shares_equivalent 替换为用户约当股数（= 持有份额 × 每份基金含股票约当数值）
            # 前端「约当数量」列显示此值
            "shares_equivalent": round(user_hold_shares, 4),
            "user_hold_shares": round(user_hold_shares, 4),
            "user_hold_value": round(user_hold_value, 4),
            # 前端期望字段名：估算市值 = user_hold_value
            "est_market_value_cny": round(user_hold_value, 4),
        })

    return {
        **public_detail,
        "funds": funds_joined,
        "constituents": constituents_joined,
        "total_user_drill_shares": round(total_user_drill_shares, 4),
    }


def get_all_drill_constituents(db: Session, as_of: _date, user_id: int) -> dict | None:
    """跨所有可下钻指数聚合成分股（按 effective user 隔离）。

    用与 get_drill_detail 相同的双币种算法，跨所有可下钻指数按 stock_code 合并，
    含 CASH 行（现金-下钻）。供「全持仓页面」drilled 段 + 「4 口径估值对比」使用。

    数据流：
      1. public_service.get_public_cards(as_of) → 所有公共卡片（含 fund_codes + effective_date）
      2. user_service.get_user_fund_holdings(user_id, fund_codes) → 用户持仓
      3. 一次性查 FundDrillSnapshot（effective_date × 用户持有的 fund）取 per-fund × per-stock 明细
      4. join：user_hold_shares = user_quantity × shares_equivalent
         est_market_value_cny = user_hold_shares × current_price_cny（本币 CNY）
      5. 跨 fund 按 stock_code 聚合（shares_equivalent / est_market_value_cny 求和，
         价格/估值字段取首个非空值，indices 累加）

    双币种规则 (2026-06-25)：est_market_value_cny 用本币(CNY)价 current_price_cny 算，
    保证 A 股/H 股量纲一致。本币字段在公共数据层算好存表，此处直接取。

    返回结构：
        {
            "as_of": "2026-06-25",
            "stocks": [
                {
                    "stock_code": "600519.SH", "stock_name": "贵州茅台",
                    "is_cash": False,
                    "shares_equivalent": 0.5,           # 用户约当股数
                    "baseline_price": 1500.0,            # 原币基准价
                    "current_price": 1600.0,             # 原币当日价
                    "baseline_price_cny": 1500.0,        # 本币基准价
                    "current_price_cny": 1600.0,         # 本币当日价
                    "est_market_value_cny": 800.0,       # = shares × current_price_cny（本币 CNY）
                    "pe_ttm": ..., "pb_mrq": ..., "ps_ttm": ..., "dividend_yield": ...,
                    "pe_ttm_dynamic": ..., "pb_mrq_dynamic": ..., "ps_ttm_dynamic": ...,
                    "currency": "CNY", "fx_rate": 1.0,
                    "indices": ["000300", "000905"],    # 来自哪些指数
                },
                ...
            ],
            "count": N,
        }
    """
    # 1. 拿所有公共卡片（含 fund_codes 列表 + effective_date，已做日期回退）
    public_cards = public_service.get_public_cards(db, as_of)
    if not public_cards:
        return None

    # 2. 收集所有 fund_codes，调用户层拿持仓
    all_fund_codes: set[str] = set()
    for card in public_cards:
        all_fund_codes.update(card["fund_codes"])
    user_holdings = user_service.get_user_fund_holdings(db, user_id, list(all_fund_codes))
    if not user_holdings:
        return None

    # 3. effective_date（从公共卡片拿，已做日期回退）
    effective_date = _date.fromisoformat(public_cards[0]["as_of"])

    # 4. 一次性查 FundDrillSnapshot（用户持有的 fund × effective_date）
    #    取 per-fund × per-stock 明细 + 估值字段（含本币 CNY 字段）
    rows = db.query(FundDrillSnapshot).filter(
        FundDrillSnapshot.as_of_date == effective_date,
        FundDrillSnapshot.fund_code.in_(list(user_holdings.keys())),
    ).all()

    # 5. 加载 fund_code → index_code 映射（用于 indices 字段，标识成分股来自哪些指数）
    fund_map = public_service._load_fund_index_map(db)  # {fund_code: (index_code, index_name)}

    # 6. 按 stock_code 聚合（跨 fund / 跨指数）
    by_stock: dict[str, dict] = {}
    for r in rows:
        h = user_holdings.get(r.fund_code)
        if not h:
            continue
        user_qty = h["quantity"]
        shares_eq = r.shares_equivalent or 0.0
        # 用户约当股数 = 持有份额 × 每份基金含股票约当数值
        user_hold_shares = user_qty * shares_eq
        # 估算市值 = 约当股数 × 本币当日价（CNY）
        price_cny = r.current_price_cny or 0.0
        est_value = user_hold_shares * price_cny

        is_cash = (r.stock_code == "CASH")
        idx_code, _ = fund_map.get(r.fund_code, ("", ""))

        if r.stock_code not in by_stock:
            by_stock[r.stock_code] = {
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "is_cash": is_cash,
                "shares_equivalent": 0.0,
                "est_market_value_cny": 0.0,
                "baseline_price": r.baseline_price,
                "current_price": r.current_price,
                "baseline_price_cny": r.baseline_price_cny,
                "current_price_cny": r.current_price_cny,
                "pe_ttm": r.pe_ttm,
                "pb_mrq": r.pb_mrq,
                "ps_ttm": r.ps_ttm,
                "dividend_yield": r.dividend_yield,
                "pe_ttm_dynamic": r.pe_ttm_dynamic,
                "pb_mrq_dynamic": r.pb_mrq_dynamic,
                "ps_ttm_dynamic": r.ps_ttm_dynamic,
                "currency": r.currency,
                "fx_rate": r.fx_rate,
                "indices": set(),
            }
        acc = by_stock[r.stock_code]
        acc["shares_equivalent"] += user_hold_shares
        acc["est_market_value_cny"] += est_value
        acc["indices"].add(idx_code)
        # 价格/估值字段取首个非空值（同一股票跨基金/指数应一致）
        for k in ("baseline_price", "current_price", "baseline_price_cny", "current_price_cny",
                  "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield",
                  "pe_ttm_dynamic", "pb_mrq_dynamic", "ps_ttm_dynamic", "currency", "fx_rate"):
            if acc.get(k) is None and getattr(r, k, None) is not None:
                acc[k] = getattr(r, k)

    # 7. 整理输出
    stocks = []
    for s in by_stock.values():
        s["indices"] = sorted(s["indices"])
        s["shares_equivalent"] = round(s["shares_equivalent"], 4)
        s["est_market_value_cny"] = round(s["est_market_value_cny"], 4)
        stocks.append(s)
    # 按估算市值降序，现金-下钻行排末尾
    stocks.sort(key=lambda r: (r.get("is_cash", False), -r["est_market_value_cny"]))

    return {
        "as_of": effective_date.isoformat(),
        "stocks": stocks,
        "count": len(stocks),
    }


def compute_scope_metrics(stocks: list[dict]) -> dict:
    """用与 drill_public_service.get_public_cards 完全一致的算法计算口径指标。

    用于「4 口径估值对比」（drilled / a_only / h_only），保证下钻卡片与全持仓 4 口径
    卡片数值一致。

    算法（调和平均 PE/PB/PS + 算术平均 DY，weight_basis 用基准日 CNY 金额）：
      weight_basis = shares_equivalent × baseline_price_cny
      price_ratio  = current_price_cny / baseline_price_cny
      pe_dyn = pe_ttm_dynamic if pe_ttm_dynamic else (pe_ttm × price_ratio)
      pb_dyn = pb_mrq_dynamic if pb_mrq_dynamic else (pb_mrq × price_ratio)
      ps_dyn = ps_ttm_dynamic if ps_ttm_dynamic else (ps_ttm × price_ratio)
      dy_dyn = dividend_yield / price_ratio   （股息率 = 分红/股价，股价涨则 DY 降）
      virt_pe = Σ weight_basis / pe_dyn   →   weighted_pe = weight_basis_sum / virt_pe
      sum_dy  = Σ weight_basis × dy_dyn   →   weighted_dy = sum_dy / weight_basis_sum

    跳过 CASH 行（is_cash=True，现金无盈利/净资产/营收/分红）。

    返回：
        {
            "stock_count": N,
            "total_amount_cny": ...,           # Σ est_market_value_cny（当日 CNY 市值，用于前端显示金额+占比）
            "weighted_pe": ...,
            "weighted_pb": ...,
            "weighted_ps": ...,
            "weighted_dividend_yield": ...,
        }
    """
    weight_basis_sum = 0.0
    virt_pe = virt_pb = virt_ps = 0.0
    sum_dy_weighted = 0.0
    total_amount_cny = 0.0
    stock_count = 0

    for s in stocks:
        if s.get("is_cash"):
            continue
        shares_eq = s.get("shares_equivalent") or 0.0
        baseline_price_cny = s.get("baseline_price_cny") or 0.0
        current_price_cny = s.get("current_price_cny") or 0.0
        if not (shares_eq > 0 and baseline_price_cny > 0 and current_price_cny > 0):
            continue
        weight_basis = shares_eq * baseline_price_cny
        price_ratio = current_price_cny / baseline_price_cny

        # 动态估值：优先用持久化 dynamic 字段（来自估值表，基于最新收盘价调整），
        # fallback 到基准日值 × price_ratio 实时算
        pe_dyn = s.get("pe_ttm_dynamic")
        if not pe_dyn:
            pe_v = s.get("pe_ttm")
            pe_dyn = (pe_v * price_ratio) if pe_v else None
        pb_dyn = s.get("pb_mrq_dynamic")
        if not pb_dyn:
            pb_v = s.get("pb_mrq")
            pb_dyn = (pb_v * price_ratio) if pb_v else None
        ps_dyn = s.get("ps_ttm_dynamic")
        if not ps_dyn:
            ps_v = s.get("ps_ttm")
            ps_dyn = (ps_v * price_ratio) if ps_v else None
        # 股息率无 dynamic 字段，用 dividend_yield / price_ratio 实时算
        dy_v = s.get("dividend_yield")
        dy_dyn = (dy_v / price_ratio) if dy_v else None

        weight_basis_sum += weight_basis
        total_amount_cny += shares_eq * current_price_cny  # 当日 CNY 市值
        stock_count += 1
        # 调和平均：累加"利润贡献"weight_basis / pe_dyn
        if pe_dyn and pe_dyn > 0:
            virt_pe += weight_basis / pe_dyn
        if pb_dyn and pb_dyn > 0:
            virt_pb += weight_basis / pb_dyn
        if ps_dyn and ps_dyn > 0:
            virt_ps += weight_basis / ps_dyn
        # 算术平均：累加 weight_basis × dy_dyn
        if dy_dyn is not None:
            sum_dy_weighted += weight_basis * dy_dyn

    return {
        "stock_count": stock_count,
        "total_amount_cny": round(total_amount_cny, 4),
        "weighted_pe": round(weight_basis_sum / virt_pe, 4) if virt_pe else None,
        "weighted_pb": round(weight_basis_sum / virt_pb, 4) if virt_pb else None,
        "weighted_ps": round(weight_basis_sum / virt_ps, 4) if virt_ps else None,
        "weighted_dividend_yield": round(sum_dy_weighted / weight_basis_sum, 4) if weight_basis_sum else None,
    }
