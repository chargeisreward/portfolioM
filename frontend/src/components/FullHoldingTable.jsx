import React, { useEffect, useMemo, useState } from 'react'
import { getFullHolding } from '../api'

const SOURCE_LABELS = {
  drilled_fund: '下钻',
  direct_stock: '直持',
  undrilled_fund: '未下钻',
  cash: '现金',
}

const fmtAmount = (v) => {
  if (v == null) return '-'
  return Math.round(v).toLocaleString('en-US')
}
const fmtPct = (v) => (v == null ? '-' : v.toFixed(2) + '%')
const fmtNum = (v, d = 2) => (v == null ? '-' : v.toFixed(d))

/**
 * FullHoldingTable — all underlying stocks from full_holding_snapshot,
 * styled to match OverviewPanel's holdings table format.
 * Sorted by amount_cny desc; columns: 代码 / 名称 / 来源 / 行业 / 金额 /
 * 权重% / PE / PB / PS / 一致预期EPS.
 */
export default function FullHoldingTable({ bizDate }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState('amount_cny')
  const [sortDir, setSortDir] = useState('desc')
  const [sourceFilter, setSourceFilter] = useState('all')

  useEffect(() => {
    if (!bizDate) return
    setLoading(true)
    getFullHolding(bizDate)
      .then(d => { setRows(d || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [bizDate])

  const totalAmount = useMemo(
    () => rows.reduce((s, r) => s + (r.amount_cny || 0), 0),
    [rows],
  )

  const filtered = useMemo(() => {
    let x = sourceFilter === 'all' ? rows : rows.filter(r => r.source_type === sourceFilter)
    x = [...x].sort((a, b) => {
      const av = a[sortKey] || 0
      const bv = b[sortKey] || 0
      return sortDir === 'desc' ? bv - av : av - bv
    })
    return x
  }, [rows, sortKey, sortDir, sourceFilter])

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const sources = useMemo(() => {
    const s = new Set(rows.map(r => r.source_type))
    return Array.from(s)
  }, [rows])

  if (!bizDate) return <div className="empty">业务日期未就绪</div>
  if (loading) return <div className="empty">加载全持仓数据…</div>
  if (!rows.length) return <div className="empty">无全持仓数据</div>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span className="section-title">全持仓 — {filtered.length} 只底层证券</span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>来源筛选:</span>
          <select value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}>
            <option value="all">全部 ({rows.length})</option>
            {sources.map(s => (
              <option key={s} value={s}>{SOURCE_LABELS[s] || s} ({rows.filter(r => r.source_type === s).length})</option>
            ))}
          </select>
        </div>
      </div>

      <div className="table-wrap" style={{ maxHeight: 600, overflowY: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ cursor: 'pointer' }} onClick={() => toggleSort('stock_code')}>代码</th>
              <th>名称</th>
              <th style={{ textAlign: 'center' }}>来源</th>
              <th>行业(L1)</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('amount_cny')}>金额(CNY)</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('shares')}>股数</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('est_market_value_cny')}>占比</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('est_deviation_pct')}>估算偏差%</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('pct_change_3m')}>3月涨跌%</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('pe_ttm_dynamic')}>PE</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('pb_mrq_dynamic')}>PB</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('ps_ttm_dynamic')}>PS</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleSort('dividend_yield')}>股息率%</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => {
              // 占比 = estimated market value / sum of all estimated market values in current filter
              const totalEst = filtered.reduce((s, x) => s + (x.est_market_value_cny ?? x.amount_cny ?? 0), 0)
              const ratio = totalEst > 0 ? (((r.est_market_value_cny ?? r.amount_cny) || 0) / totalEst * 100) : 0
              const dev = r.est_deviation_pct
              const devColor = dev > 0 ? 'var(--chart-up)' : dev < 0 ? 'var(--chart-down)' : 'var(--text-secondary)'
              const m3 = r.pct_change_3m
              const m3Color = m3 > 0 ? 'var(--chart-up)' : m3 < 0 ? 'var(--chart-down)' : 'var(--text-secondary)'
              return (
                <tr key={`${r.stock_code}-${r.source_holding_code}-${i}`}>
                  <td title={r.stock_code} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.stock_code}
                  </td>
                  <td title={r.stock_name} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.stock_name || '-'}
                  </td>
                  <td style={{ textAlign: 'center', color: 'var(--text-secondary)', fontSize: 11 }}>
                    {SOURCE_LABELS[r.source_type] || r.source_type}
                  </td>
                  <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>
                    {r.industry_l1 || '其他'}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', fontWeight: 600 }}>
                    {fmtAmount(r.amount_cny)}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                    {r.shares != null ? r.shares.toLocaleString() : '-'}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>
                    {fmtPct(ratio)}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: devColor, fontWeight: 600 }}>
                    {dev != null && dev !== 0 ? (dev > 0 ? '+' : '') + dev.toFixed(2) + '%' : '-'}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: m3Color }}>
                    {m3 != null ? (m3 > 0 ? '+' : '') + m3.toFixed(2) + '%' : '-'}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                    {fmtNum(r.pe_ttm_dynamic)}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                    {fmtNum(r.pb_mrq_dynamic)}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                    {fmtNum(r.ps_ttm_dynamic)}
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                    {fmtNum(r.dividend_yield, 2)}
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: '1px solid var(--border-strong)', fontWeight: 600 }}>
              <td colSpan={4} style={{ color: 'var(--text-muted)', fontSize: 11 }}>合计</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', fontWeight: 700 }}>
                {fmtAmount(filtered.reduce((s, r) => s + (r.amount_cny || 0), 0))}
              </td>
              <td colSpan={3}></td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)', fontWeight: 600 }}>
                100.00%
              </td>
              <td colSpan={6}></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  )
}