import React, { useEffect, useState } from 'react'
import { getDimensionDrilled } from '../api'

const fmtAmount = (v) => {
  if (v == null) return '-'
  return Math.round(v).toLocaleString('en-US')
}
const fmtNum = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d))
const fmtPct = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d) + '%')
const fmtShares = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))

// 中国习惯：高/正 = 红，低/负 = 绿
const COLOR_HIGH = 'var(--down, #ef4444)'
const COLOR_LOW = 'var(--up, #22c55e)'

const DIM_LABELS = {
  swy1: '申万L1',
  swy2: '申万L2',
  swy3: '申万L3',
  swy4: '申万L4',
  csi1: '中证L1',
  csi2: '中证L2',
  csi3: '中证L3',
  csi4: '中证L4',
  se1: '战略新兴L1',
  se2: '战略新兴L2',
  se3: '战略新兴L3',
  se4: '战略新兴L4',
  chain: '产业链',
  growth_tier: '增长分层',
  competition: '竞争格局',
}

export default function DrilledDimensionPanel({ dim, bizDate, market = 'A+H', label: labelProp }) {
  const [data, setData] = useState(null)
  const [expandedKey, setExpandedKey] = useState(null)
  const label = labelProp || DIM_LABELS[dim] || dim

  useEffect(() => {
    if (!bizDate) return
    setExpandedKey(null)
    getDimensionDrilled(dim, bizDate, market)
      .then(setData)
      .catch(() => setData(null))
  }, [dim, bizDate, market])

  if (!bizDate) return <div className="empty">业务日期未就绪</div>
  if (!data) return <div className="empty">加载 {label}（下钻证券）数据…</div>
  if (!data.portfolio || data.portfolio.length === 0) {
    return <div className="empty">{label} 暂无下钻证券数据</div>
  }

  const csi300ByKey = Object.fromEntries(data.csi300.map(r => [r.key, r]))
  const totalAmount = data.totals?.portfolio?.amount_cny || 1

  return (
    <div className="industry-breakdown-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span className="section-title">{label} — 仅下钻证券 · {data.portfolio.length} 项</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"GeistMono",monospace' }}>
          下钻合计 {fmtAmount(totalAmount)} CNY · 组合PE {fmtNum(data.totals?.portfolio?.pe_weighted, 1)} · CSI300 PE {fmtNum(data.totals?.csi300?.pe_weighted, 1)}
        </span>
      </div>

      <div className="table-wrap" style={{ maxHeight: 600, overflowY: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>{label}</th>
              <th style={{ textAlign: 'right' }}>只数</th>
              <th style={{ textAlign: 'right' }}>金额(CNY)</th>
              <th style={{ textAlign: 'right' }}>权重%</th>
              <th style={{ textAlign: 'right' }}>组合PE</th>
              <th style={{ textAlign: 'right' }}>CSI300权重%</th>
              <th style={{ textAlign: 'right' }}>CSI300 PE</th>
              <th style={{ textAlign: 'right' }}>权重差异%</th>
              <th style={{ textAlign: 'right' }}>PE差异</th>
            </tr>
          </thead>
          <tbody>
            {data.portfolio.map(row => {
              const csi = csi300ByKey[row.key]
              const csiWeight = csi?.weight_pct ?? null
              const csiPe = csi?.pe_weighted ?? null
              const weightDiff = (csiWeight != null) ? (row.weight_pct - csiWeight) : null
              const peDiff = (csiPe != null && row.pe_weighted != null) ? (row.pe_weighted - csiPe) : null
              const isOpen = expandedKey === row.key

              const weightColor = (csiWeight != null)
                ? (row.weight_pct > csiWeight ? COLOR_HIGH : COLOR_LOW)
                : undefined
              const peColor = (csiPe != null && row.pe_weighted != null)
                ? (row.pe_weighted > csiPe ? COLOR_HIGH : COLOR_LOW)
                : undefined
              const weightDiffColor = (weightDiff != null)
                ? (weightDiff > 0 ? COLOR_HIGH : COLOR_LOW)
                : undefined
              const peDiffColor = (peDiff != null)
                ? (peDiff > 0 ? COLOR_HIGH : COLOR_LOW)
                : undefined

              return (
                <React.Fragment key={row.key}>
                  <tr
                    className={isOpen ? 'expanded' : ''}
                    style={{ cursor: 'pointer', background: isOpen ? 'var(--bg-soft, rgba(255,255,255,0.04))' : undefined }}
                    onClick={() => setExpandedKey(prev => prev === row.key ? null : row.key)}
                  >
                    <td title={row.key} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {isOpen ? '▼ ' : '▸ '}{row.key}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{row.stock_count}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', fontWeight: 600 }}>
                      {fmtAmount(row.amount_cny)}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: weightColor }}>
                      {fmtPct(row.weight_pct, 1)}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: peColor }}>
                      {fmtNum(row.pe_weighted, 1)}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>
                      {fmtPct(csiWeight, 1)}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>
                      {fmtNum(csiPe, 1)}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: weightDiffColor }}>
                      {weightDiff != null ? (weightDiff > 0 ? '+' : '') + fmtNum(weightDiff, 1) + '%' : '-'}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: peDiffColor }}>
                      {peDiff != null ? (peDiff > 0 ? '+' : '') + fmtNum(peDiff, 1) : '-'}
                    </td>
                  </tr>

                  {isOpen && (
                    <tr>
                      <td colSpan={9} style={{ padding: 0, background: 'rgba(0,0,0,0.15)' }}>
                        <DrilledStockDetailTable stocks={data.stock_details?.[row.key] || []} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: '1px solid var(--border-strong)', fontWeight: 600 }}>
              <td style={{ color: 'var(--text-muted)', fontSize: 11 }}>合计</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{data.totals?.portfolio?.stock_count || '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', fontWeight: 700 }}>
                {fmtAmount(data.totals?.portfolio?.amount_cny)}
              </td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>100.0%</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                {fmtNum(data.totals?.portfolio?.pe_weighted, 1)}
              </td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>
                {fmtPct(data.totals?.csi300?.weight_pct ?? 100, 1)}
              </td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>
                {fmtNum(data.totals?.csi300?.pe_weighted, 1)}
              </td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>0.0%</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                {(() => {
                  const p = data.totals?.portfolio?.pe_weighted
                  const c = data.totals?.csi300?.pe_weighted
                  if (p == null || c == null) return '-'
                  const d = p - c
                  return (d > 0 ? '+' : '') + fmtNum(d, 1)
                })()}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  )
}

function DrilledStockDetailTable({ stocks }) {
  if (!stocks || stocks.length === 0) {
    return <div style={{ padding: 12, fontSize: 12, color: 'var(--text-muted)' }}>暂无明细</div>
  }
  return (
    <div style={{ padding: 10 }}>
      <table className="data-table" style={{ fontSize: 12 }}>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th style={{ textAlign: 'right' }}>约当数量</th>
            <th style={{ textAlign: 'right' }}>最近收盘价</th>
            <th style={{ textAlign: 'right' }}>资产值</th>
            <th style={{ textAlign: 'right' }}>权重%</th>
            <th style={{ textAlign: 'right' }}>PE</th>
            <th style={{ textAlign: 'right' }}>PS</th>
            <th style={{ textAlign: 'right' }}>PB</th>
          </tr>
        </thead>
        <tbody>
          {stocks
            .sort((a, b) => (b.amount_cny || 0) - (a.amount_cny || 0))
            .map(s => (
              <tr key={s.stock_code}>
                <td style={{ fontFamily: '"GeistMono",monospace' }}>{s.stock_code}</td>
                <td>{s.stock_name || '-'}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtShares(s.shares_equivalent)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(s.current_price_cny, 2)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtAmount(s.amount_cny)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>
                  {fmtPct(s.weight_pct, 2)}
                </td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(s.pe_ttm, 1)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(s.ps_ttm, 1)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(s.pb_mrq, 1)}</td>
              </tr>
            ))}
        </tbody>
      </table>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4, fontFamily: '"GeistMono",monospace' }}>
        * 权重以下钻证券合计为分母
      </div>
    </div>
  )
}
