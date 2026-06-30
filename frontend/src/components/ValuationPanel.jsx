import React, { useState, useEffect, useCallback } from 'react'
import * as api from '../api'
import ShareBar from './ShareBar'

/**
 * 估值表面板（独立页面）— 2026-06-27 整体优化。
 *
 * 核心特性：
 * 1. 截面存储：每日持仓+股价+市值+关键指标，锁定后不重算
 * 2. 锁定 badge：股价确认为当天收盘价 → 锁定，否则未锁定
 * 3. 类型/主题 toggle：现金第一行，按类型或主题分组列示，组内缩进 2 格
 * 4. KPI 卡片：同总览第一排卡片，计算口径相同
 * 5. 数字右对齐 + GeistMono 字体
 */

// ---- 标签映射（与 OverviewPanel 保持一致）----
const CAT_SHORT = {
  a_share_equity: 'A基主动', a_share_etf: 'A基指数', bond: '债券', gold: '黄金',
  hk_equity: '港股', qdii_equity: 'QDII', us_stock: '美股', us_etf: '美股E',
  cash: '现金', commodity: '商品',
}
const CAT_FULL = {
  a_share_equity: 'A股主动基金', a_share_etf: 'A股指数基金', bond: '债券', gold: '黄金',
  hk_equity: '港股', qdii_equity: 'QDII基金', us_stock: '美股', us_etf: '美股ETF',
  cash: '现金', commodity: '商品',
}
const TYPE2_LABELS = { dividend: '红利', emerging: '新兴产业', gold: '黄金' }
const type2Display = (raw) => TYPE2_LABELS[raw] || raw

// 交易类型中文映射
const TRADE_TYPE_LABELS = { buy: '申购', sell: '赎回', dividend: '分红', others: '其他' }

// ---- 数字格式化（与 OverviewPanel 对齐）----
const fmtAmount = (v, symbol = '¥') => {
  if (v == null || isNaN(v)) return '—'
  return symbol + Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
const fmtQty = (v) => {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}
const fmtPct = (v) => {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toFixed(2) + '%'
}
const fmtPrice = (v) => {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toFixed(4)
}
// 当日交易专用：强制2位小数（带千分位）
const fmtQty2 = (v) => {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
const fmtAmount2 = (v, symbol = '¥') => {
  if (v == null || isNaN(v)) return '—'
  return symbol + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// 数字右对齐样式（GeistMono 字体，项目约定）
const numStyle = { textAlign: 'right', fontFamily: '"GeistMono",monospace' }

// 占比可视化柱状条：使用共享组件 ./ShareBar（连续渲染，5 格 × 2%，>10% 显示 +）

// 组汇总行整行字体色（浅金色）— App.css .data-table td { color: var(--text) } 会覆盖 tr 继承，
// 故需在每个 td 上显式设置 color
const GROUP_COLOR = '#ffd54f'
const groupRowStyle = { color: GROUP_COLOR }
const groupTdStyle = { color: GROUP_COLOR }
const groupNumStyle = { ...numStyle, color: GROUP_COLOR }

export default function ValuationPanel() {
  const [snapshotRange, setSnapshotRange] = useState({ start_date: null, end_date: null })
  const [selectedDate, setSelectedDate] = useState('')
  const [snapshot, setSnapshot] = useState(null)  // {as_of_date, is_locked, locked_at, holdings[]}
  const [dailyTrades, setDailyTrades] = useState([])
  const [kpi, setKpi] = useState(null)  // 后端 KPI（PE/涨跌幅/科技占比，口径同 OverviewPanel）
  const [viewMode, setViewMode] = useState('type')  // 'type' | 'theme'
  const [loading, setLoading] = useState(false)

  // ---- 挂载时获取估值截面日期范围 ----
  useEffect(() => {
    api.getValuationRange().then(data => {
      setSnapshotRange(data)
      if (data.end_date) {
        setSelectedDate(data.end_date)
      }
    }).catch(() => {})
  }, [])

  // ---- 选中日期变化时加载估值截面 + 当日交易 + KPI ----
  // KPI 调 /api/valuation/kpi：基于历史持仓 snapshot(as_of_date) + 历史公共数据 + 当前证券主数据
  //   反映 as_of_date 当日真实情况（不再用当前 Holding + 实时 PriceCache）
  const loadData = useCallback((asOf) => {
    if (!asOf) return
    setLoading(true)
    Promise.all([
      api.getValuationSnapshot({ as_of: asOf }),
      api.getDailyTrades({ as_of: asOf }),
      api.getValuationKpi(asOf).catch(() => null),
    ]).then(([snap, trades, kpiData]) => {
      setSnapshot(snap)
      setDailyTrades(trades || [])
      setKpi(kpiData?.values || kpiData || null)
    }).catch(() => {
      setSnapshot(null)
      setDailyTrades([])
      setKpi(null)
    }).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (selectedDate) loadData(selectedDate)
  }, [selectedDate, loadData])

  // ---- 估值表合计 ----
  const holdings = snapshot?.holdings || []
  const totalAmountCny = holdings.reduce((s, r) => s + (r.amount_cny || 0), 0)

  // KPI 数据来源：/api/valuation/kpi 端点（基于历史持仓 snapshot + 历史公共数据 + 当前证券主数据）
  // 反映 as_of_date 当日真实情况，返回字段：total_amount_cny / drilled_stock_count /
  // drilled_available / portfolio_pe_weighted / daily_change_pct / intraday_change_pct / tech_weight_pct

  // 现金行单独提取（永远第一行）
  const cashRow = holdings.find(h => h.is_cash) || null
  // 非现金持仓 — 按 security_code 聚合（groupby code — 2026-06-30 用户反馈）
  // 底层持仓不动（holding_daily_snapshot 表保持原样），仅 UI 显示聚类
  //   - 数量：求和
  //   - 单价·原 / 单价·本：按数量加权平均
  //   - 金额·本：求和
  //   - 名称/类型/主题：取首个非空
  const mergedByCode = new Map()
  for (const h of holdings) {
    if (h.is_cash) continue
    const code = h.security_code
    if (!code) continue
    const acc = mergedByCode.get(code) || {
      security_code: code,
      security_name: h.security_name,
      asset_type: h.asset_type,
      type2: h.type2,
      currency: h.currency,
      is_cash: false,
      _qty_sum: 0,
      _price_num: 0,         // Σ(qty × price) 用于加权均价
      _price_cny_num: 0,     // Σ(qty × price_cny) 用于加权均价
      _amount_cny_sum: 0,
      _price_set: false,     // 是否有任何 qty>0
      _holding_uids: [],     // 保留来源 holding_uid 列表，便于追溯
    }
    if (!acc.security_name && h.security_name) acc.security_name = h.security_name
    if (!acc.asset_type && h.asset_type) acc.asset_type = h.asset_type
    if (!acc.type2 && h.type2) acc.type2 = h.type2
    if (!acc.currency && h.currency) acc.currency = h.currency
    const qty = Number(h.quantity || 0)
    const price = Number(h.price || 0)
    const priceCny = Number(h.price_cny || 0)
    acc._qty_sum += qty
    if (qty > 0) {
      acc._price_num += qty * price
      acc._price_cny_num += qty * priceCny
      acc._price_set = true
    }
    acc._amount_cny_sum += Number(h.amount_cny || 0)
    if (h.holding_uid) acc._holding_uids.push(h.holding_uid)
    mergedByCode.set(code, acc)
  }
  // 转回普通数组，标准化字段名（脱去 _ 前缀的累加器字段）
  const nonCashHoldings = Array.from(mergedByCode.values()).map(acc => ({
    security_code: acc.security_code,
    security_name: acc.security_name,
    asset_type: acc.asset_type,
    type2: acc.type2,
    currency: acc.currency,
    is_cash: false,
    quantity: acc._qty_sum,
    price: acc._price_set && acc._qty_sum > 0 ? acc._price_num / acc._qty_sum : null,
    price_cny: acc._price_set && acc._qty_sum > 0 ? acc._price_cny_num / acc._qty_sum : null,
    amount_cny: acc._amount_cny_sum,
    holding_uids: acc._holding_uids,
    holding_count: acc._holding_uids.length,
  }))

  // 按 viewMode 分组
  const groups = {}
  nonCashHoldings.forEach(h => {
    let key
    if (viewMode === 'type') {
      key = CAT_SHORT[h.asset_type] || h.asset_type || '其他'
    } else {
      key = h.type2 ? type2Display(h.type2) : '其他'
    }
    if (!groups[key]) groups[key] = { rows: [], total: 0 }
    groups[key].rows.push(h)
    groups[key].total += (h.amount_cny || 0)
  })

  // 组内按权重（金额·本）降序排列
  Object.values(groups).forEach(g => {
    g.rows.sort((a, b) => (b.amount_cny || 0) - (a.amount_cny || 0))
  })

  // 组全名（用于组汇总行的名称列）
  const groupFullName = (groupName) => {
    if (viewMode === 'type') {
      return CAT_FULL[Object.keys(CAT_SHORT).find(k => CAT_SHORT[k] === groupName)] || groupName
    }
    return groupName  // 主题模式：名称就用主题名
  }

  return (
    <div className="raised" style={{ padding: 16 }}>
      {/* 日期控件 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>选择日期：</label>
        <input
          type="date"
          className="ig"
          value={selectedDate}
          min={snapshotRange.start_date || undefined}
          max={snapshotRange.end_date || undefined}
          onChange={e => setSelectedDate(e.target.value)}
          disabled={!snapshotRange.end_date}
          style={{ width: 140 }}
        />
        {!snapshotRange.end_date && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>（尚无截面，请先提交交易）</span>
        )}
      </div>

      {/* KPI 卡片（基于历史持仓 snapshot + 历史公共数据 + 当前证券主数据）*/}
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className="kpi-card">
          <div className="kpi-label">总资产</div>
          <div className="kpi-value">{kpi?.total_amount_cny != null ? fmtAmount(kpi.total_amount_cny) : '—'}</div>
          <div className="kpi-sub">CNY</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">穿透股票</div>
          <div className="kpi-value">{
            kpi?.drilled_available
              ? (kpi.drilled_stock_count != null && kpi.drilled_stock_count > 0
                  ? kpi.drilled_stock_count
                  : '缺指数构成和权重')
              : '缺指数构成和权重'
          }</div>
          <div className="kpi-sub">{kpi?.fund_count != null ? `${kpi.fund_count}基金` : '—'}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">组合PE</div>
          <div className="kpi-value">{kpi?.portfolio_pe_weighted != null ? kpi.portfolio_pe_weighted.toFixed(1) : '缺指数构成和权重'}</div>
          <div className="kpi-sub">300: {kpi?.csi300_pe != null ? kpi.csi300_pe.toFixed(1) : '—'}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">上日涨跌幅</div>
          <div className="kpi-value" style={{
            color: kpi?.daily_change_pct == null ? undefined
                   : (kpi.daily_change_pct > 0 ? 'var(--up)'
                   : kpi.daily_change_pct < 0 ? 'var(--down)' : undefined),
            fontWeight: 600,
          }}>
            {kpi?.daily_change_pct != null
              ? (kpi.daily_change_pct > 0 ? '+' : '') + kpi.daily_change_pct.toFixed(2) + '%'
              : '缺历史数据'}
          </div>
          <div className="kpi-sub" style={{ fontSize: 10 }}>
            {kpi?.daily_change_breakdown?.latest_trade_date && kpi?.daily_change_breakdown?.prev_trade_date
              ? `${kpi.daily_change_breakdown.latest_trade_date} vs ${kpi.daily_change_breakdown.prev_trade_date}`
              : '—'}
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">当日涨跌幅</div>
          <div className="kpi-value" style={{
            color: kpi?.intraday_change_pct == null ? undefined
                   : (kpi.intraday_change_pct > 0 ? 'var(--up)'
                   : kpi.intraday_change_pct < 0 ? 'var(--down)' : undefined),
            fontWeight: 600,
          }}>
            {kpi?.intraday_change_pct != null
              ? (kpi.intraday_change_pct > 0 ? '+' : '') + kpi.intraday_change_pct.toFixed(2) + '%'
              : '缺历史数据'}
          </div>
          <div className="kpi-sub" style={{ fontSize: 10 }}>
            {kpi?.intraday_breakdown?.latest_trade_date && kpi?.intraday_breakdown?.prev_trade_date
              ? `${kpi.intraday_breakdown.latest_trade_date} vs ${kpi.intraday_breakdown.prev_trade_date}`
              : '—'}
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">科技占比</div>
          <div className="kpi-value">{kpi?.tech_weight_pct != null ? kpi.tech_weight_pct.toFixed(1) + '%' : '—'}</div>
          <div className="kpi-sub" style={{ fontSize: 10 }}>
            {kpi?.tech_weight_breakdown
              ? `新兴 ${(kpi.tech_weight_breakdown.emerging_cny/10000).toFixed(0)}w + 美科 ${(kpi.tech_weight_breakdown.us_tech_cny/10000).toFixed(0)}w`
              : '—'}
          </div>
        </div>
      </div>

      {/* 当日交易 */}
      <div style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ fontSize: 13, marginBottom: 6 }}>当日交易</div>
        {loading ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>加载中...</div>
        ) : dailyTrades.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>当日无交易</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 100 }}>代码</th>
                  <th>名称</th>
                  <th style={{ width: 70 }}>类型</th>
                  <th style={{ width: 100, ...numStyle }}>确认份额</th>
                  <th style={{ width: 100, ...numStyle }}>确认金额</th>
                </tr>
              </thead>
              <tbody>
                {dailyTrades.map((t, idx) => (
                  <tr key={idx}>
                    <td>{t.security_code}</td>
                    <td>{t.security_name || '—'}</td>
                    <td>{TRADE_TYPE_LABELS[t.trade_type] || t.trade_type}</td>
                    <td style={numStyle}>{fmtQty2(t.confirmed_shares)}</td>
                    <td style={numStyle}>{fmtAmount2(t.confirmed_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 估值表（持仓快照）— 标题 + 锁定 badge */}
      <div>
        <div className="section-title" style={{ fontSize: 13, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span>估值表</span>
          {snapshot && (
            snapshot.is_locked ? (
              <span style={{
                fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 500,
                background: 'rgba(46, 160, 67, 0.12)', color: '#2ea043',
                border: '1px solid rgba(46, 160, 67, 0.3)',
              }}>锁定</span>
            ) : (
              <span style={{
                fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 500,
                background: 'rgba(255, 140, 0, 0.12)', color: '#ff8c00',
                border: '1px solid rgba(255, 140, 0, 0.3)',
              }}>未锁定</span>
            )
          )}
        </div>

        {/* 类型/主题 toggle（"估值表"下方，表格上方） */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"GeistMono",monospace', letterSpacing: 0.5 }}>视图</span>
          <button
            onClick={() => setViewMode('type')}
            className={viewMode === 'type' ? 'cur-btn on' : 'cur-btn'}
            style={{ fontSize: 10 }}
          >类型</button>
          <button
            onClick={() => setViewMode('theme')}
            className={viewMode === 'theme' ? 'cur-btn on' : 'cur-btn'}
            style={{ fontSize: 10 }}
          >主题</button>
        </div>

        {loading ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>加载中...</div>
        ) : holdings.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>无持仓快照</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 110 }}>代码</th>
                  <th>名称</th>
                  <th style={{ width: 90, ...numStyle }}>数量</th>
                  <th style={{ width: 90, ...numStyle }}>单价·原</th>
                  <th style={{ width: 90, ...numStyle }}>单价·本</th>
                  <th style={{ width: 110, ...numStyle }}>金额·本</th>
                  <th style={{ width: 70, ...numStyle }}>占比</th>
                  <th style={{ width: 90, textAlign: 'left' }}>占比图</th>
                </tr>
              </thead>
              <tbody>
                {/* 现金行（永远第一行） */}
                {cashRow && (
                  <tr style={{ fontStyle: 'italic', color: 'var(--text-secondary)' }}>
                    <td>{cashRow.security_code}</td>
                    <td>{cashRow.security_name || '现金'}</td>
                    <td style={numStyle}>{fmtQty(cashRow.quantity)}</td>
                    <td style={numStyle}>—</td>
                    <td style={numStyle}>—</td>
                    <td style={numStyle}>{fmtAmount(cashRow.amount_cny)}</td>
                    <td style={numStyle}>{fmtPct(totalAmountCny > 0 ? cashRow.amount_cny / totalAmountCny * 100 : 0)}</td>
                    <td style={{textAlign: 'left'}}><ShareBar pct={totalAmountCny > 0 ? cashRow.amount_cny / totalAmountCny * 100 : 0} /></td>
                  </tr>
                )}

                {/* 分组渲染：组汇总行 + 组内证券行（缩进 2 格） */}
                {Object.entries(groups).map(([groupName, g]) => (
                  <React.Fragment key={groupName}>
                    {/* 组汇总行 — 浅金色字体（每个 td 显式设置 color，覆盖 .data-table td 的 color） */}
                    <tr style={{ fontWeight: 600, background: 'var(--bg-raised, rgba(0,0,0,0.02))', ...groupRowStyle }}>
                      <td style={groupTdStyle}>{groupName}</td>
                      <td style={groupTdStyle}>{groupFullName(groupName)}</td>
                      <td style={groupNumStyle}>—</td>
                      <td style={groupNumStyle}>—</td>
                      <td style={groupNumStyle}>—</td>
                      <td style={groupNumStyle}>{fmtAmount(g.total)}</td>
                      <td style={groupNumStyle}>{fmtPct(totalAmountCny > 0 ? g.total / totalAmountCny * 100 : 0)}</td>
                      <td style={{textAlign: 'left'}}><ShareBar pct={totalAmountCny > 0 ? g.total / totalAmountCny * 100 : 0} /></td>
                    </tr>
                    {/* 组内证券行（缩进 2 字符 = paddingLeft: 16px） */}
                    {g.rows.map((h, idx) => (
                      <tr key={`${h.security_code}#${idx}`}>
                        <td style={{ paddingLeft: 16 }}>
                          {h.security_code}
                          {h.holding_count > 1 && (
                            <span
                              title={`合并自 ${h.holding_count} 条持仓批次（底层数据未变）`}
                              style={{
                                marginLeft: 6, fontSize: 9, padding: '1px 5px',
                                borderRadius: 8, background: 'var(--bg-raised)',
                                color: 'var(--text-muted)', cursor: 'help',
                              }}
                            >×{h.holding_count}</span>
                          )}
                        </td>
                        <td style={{ paddingLeft: 16 }}>{h.security_name || '—'}</td>
                        <td style={numStyle}>{fmtQty(h.quantity)}</td>
                        <td style={numStyle}>{fmtPrice(h.price)}</td>
                        <td style={numStyle}>{fmtPrice(h.price_cny)}</td>
                        <td style={numStyle}>{fmtAmount(h.amount_cny)}</td>
                        <td style={numStyle}>{fmtPct(totalAmountCny > 0 ? h.amount_cny / totalAmountCny * 100 : 0)}</td>
                        <td style={{textAlign: 'left'}}><ShareBar pct={totalAmountCny > 0 ? h.amount_cny / totalAmountCny * 100 : 0} /></td>
                      </tr>
                    ))}
                  </React.Fragment>
                ))}
              </tbody>
              <tfoot>
                <tr style={{ fontWeight: 600, borderTop: '2px solid var(--border)' }}>
                  <td colSpan={5}>合计</td>
                  <td style={numStyle}>{fmtAmount(totalAmountCny)}</td>
                  <td style={numStyle}>100%</td>
                  <td style={{textAlign: 'left'}}><ShareBar pct={100} /></td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
