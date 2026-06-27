import React, { useState, useEffect, useCallback } from 'react'
import * as api from '../api'

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

// 数字右对齐样式（GeistMono 字体，项目约定）
const numStyle = { textAlign: 'right', fontFamily: '"GeistMono",monospace' }

export default function ValuationPanel() {
  const [snapshotRange, setSnapshotRange] = useState({ start_date: null, end_date: null })
  const [selectedDate, setSelectedDate] = useState('')
  const [snapshot, setSnapshot] = useState(null)  // {as_of_date, is_locked, locked_at, holdings[]}
  const [dailyTrades, setDailyTrades] = useState([])
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

  // ---- 选中日期变化时加载估值截面 + 当日交易 ----
  // KPI 不再调 /api/penetration/kpi（数据源不一致），改为从 snapshot.holdings 本地计算
  const loadData = useCallback((asOf) => {
    if (!asOf) return
    setLoading(true)
    Promise.all([
      api.getValuationSnapshot({ as_of: asOf }),
      api.getDailyTrades({ as_of: asOf }),
    ]).then(([snap, trades]) => {
      setSnapshot(snap)
      setDailyTrades(trades || [])
    }).catch(() => {
      setSnapshot(null)
      setDailyTrades([])
    }).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (selectedDate) loadData(selectedDate)
  }, [selectedDate, loadData])

  // ---- 估值表合计 ----
  const holdings = snapshot?.holdings || []
  const totalAmountCny = holdings.reduce((s, r) => s + (r.amount_cny || 0), 0)

  // ---- KPI 本地计算（数据源：ValuationDailySnapshot，与估值表一致）----
  // 替代原 api.getKpi → /api/penetration/kpi（读 FullHoldingSnapshot，数据源不一致导致 ¥0）
  // 计算口径对齐 OverviewPanel：总资产/持仓数/加权PE/科技占比 从 holdings 直接算；
  // 上日涨跌幅/当日涨跌幅/CSI300PE 估值截面不存储，显示 "—"。
  const kpi = React.useMemo(() => {
    if (!holdings.length) return null
    const nonCash = holdings.filter(h => !h.is_cash)
    // 基金类（穿透载体）：A股主动/指数、QDII、美股ETF
    const fundTypes = new Set(['a_share_equity', 'a_share_etf', 'qdii_equity', 'us_etf'])
    const fundCount = nonCash.filter(h => fundTypes.has(h.asset_type)).length
    // 加权 PE：sum(amount * pe) / sum(amount)，仅对 pe > 0 的非现金持仓
    let peSum = 0, peWeight = 0
    nonCash.forEach(h => {
      if (h.pe_ttm && h.pe_ttm > 0 && h.amount_cny > 0) {
        peSum += h.amount_cny * h.pe_ttm
        peWeight += h.amount_cny
      }
    })
    const portfolio_pe_weighted = peWeight > 0 ? peSum / peWeight : null
    // 科技占比：type2 in {emerging, gold}（与 OverviewPanel.tech_weight_breakdown 口径对齐）
    const emergingCny = nonCash.filter(h => h.type2 === 'emerging').reduce((s, h) => s + (h.amount_cny || 0), 0)
    const goldCny = nonCash.filter(h => h.type2 === 'gold').reduce((s, h) => s + (h.amount_cny || 0), 0)
    const techCny = emergingCny + goldCny
    const tech_weight_pct = totalAmountCny > 0 ? techCny / totalAmountCny * 100 : null
    return {
      total_amount_cny: totalAmountCny,
      drilled_stock_count: nonCash.length,  // 估值表口径：非现金持仓数
      fund_count: fundCount,
      portfolio_pe_weighted,
      csi300_pe: null,              // 估值截面不存储指数 PE
      daily_change_pct: null,       // 估值截面不存储上日涨跌
      intraday_change_pct: null,    // 估值截面不存储盘中涨跌
      tech_weight_pct,
      tech_weight_breakdown: { emerging_cny: emergingCny, gold_cny: goldCny, us_tech_cny: 0 },
    }
  }, [holdings, totalAmountCny])

  // 现金行单独提取（永远第一行）
  const cashRow = holdings.find(h => h.is_cash) || null
  // 非现金持仓
  const nonCashHoldings = holdings.filter(h => !h.is_cash)

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

      {/* KPI 卡片（同总览第一排卡片，计算口径相同） */}
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className="kpi-card">
          <div className="kpi-label">总资产</div>
          <div className="kpi-value">{kpi?.total_amount_cny != null ? fmtAmount(kpi.total_amount_cny) : '—'}</div>
          <div className="kpi-sub">CNY</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">穿透股票</div>
          <div className="kpi-value">{kpi?.drilled_stock_count != null ? kpi.drilled_stock_count : '—'}</div>
          <div className="kpi-sub">{kpi?.fund_count != null ? `${kpi.fund_count}基金` : '—'}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">组合PE</div>
          <div className="kpi-value">{kpi?.portfolio_pe_weighted != null ? kpi.portfolio_pe_weighted.toFixed(1) : '—'}</div>
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
              : '—'}
          </div>
          <div className="kpi-sub" style={{ fontSize: 10 }}>
            {kpi?.daily_change_breakdown?.latest_trade_date && kpi?.daily_change_breakdown?.prev_trade_date
              ? `${kpi.daily_change_breakdown.latest_trade_date} vs ${kpi.daily_change_breakdown.prev_trade_date}`
              : '未加载'}
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
              : '—'}
          </div>
          <div className="kpi-sub" style={{ fontSize: 10 }}>
            {kpi?.intraday_breakdown?.covered_count > 0
              ? `覆盖 ${kpi.intraday_breakdown.covered_count} 只`
              : '盘中实时'}
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">科技占比</div>
          <div className="kpi-value">{kpi?.tech_weight_pct != null ? kpi.tech_weight_pct.toFixed(1) + '%' : '—'}</div>
          <div className="kpi-sub" style={{ fontSize: 10 }}>
            {kpi?.tech_weight_breakdown
              ? `新兴 ${(kpi.tech_weight_breakdown.emerging_cny/10000).toFixed(0)}w + 黄金 ${(kpi.tech_weight_breakdown.gold_cny/10000).toFixed(0)}w`
              : '未加载'}
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
                    <td style={numStyle}>{fmtQty(t.confirmed_shares)}</td>
                    <td style={numStyle}>{fmtAmount(t.confirmed_amount)}</td>
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
                  </tr>
                )}

                {/* 分组渲染：组汇总行 + 组内证券行（缩进 2 格） */}
                {Object.entries(groups).map(([groupName, g]) => (
                  <React.Fragment key={groupName}>
                    {/* 组汇总行 */}
                    <tr style={{ fontWeight: 600, background: 'var(--bg-raised, rgba(0,0,0,0.02))' }}>
                      <td>{groupName}</td>
                      <td>{groupFullName(groupName)}</td>
                      <td style={numStyle}>—</td>
                      <td style={numStyle}>—</td>
                      <td style={numStyle}>—</td>
                      <td style={numStyle}>{fmtAmount(g.total)}</td>
                      <td style={numStyle}>{fmtPct(totalAmountCny > 0 ? g.total / totalAmountCny * 100 : 0)}</td>
                    </tr>
                    {/* 组内证券行（缩进 2 字符 = paddingLeft: 16px） */}
                    {g.rows.map((h, idx) => (
                      <tr key={`${h.security_code}#${idx}`}>
                        <td style={{ paddingLeft: 16 }}>{h.security_code}</td>
                        <td style={{ paddingLeft: 16 }}>{h.security_name || '—'}</td>
                        <td style={numStyle}>{fmtQty(h.quantity)}</td>
                        <td style={numStyle}>{fmtPrice(h.price)}</td>
                        <td style={numStyle}>{fmtPrice(h.price_cny)}</td>
                        <td style={numStyle}>{fmtAmount(h.amount_cny)}</td>
                        <td style={numStyle}>{fmtPct(totalAmountCny > 0 ? h.amount_cny / totalAmountCny * 100 : 0)}</td>
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
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
