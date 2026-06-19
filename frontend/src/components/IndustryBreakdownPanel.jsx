import React, { useEffect, useState } from 'react'
import { getDimension, getDimensionDetail } from '../api'
import IndustryDrilldownTable from './IndustryDrilldownTable'
import MetricTimeseriesChart from './MetricTimeseriesChart'

const DIM_LABELS = {
  l1: '行业(L1)',
  l2: '行业(L2)',
  chain: '产业链',
  growth_tier: '增长分层',
  competition: '竞争格局',
}

/**
 * IndustryBreakdownPanel — generic table over a single dimension.
 * Click a row → expand detail table + click any metric column header → expand trend chart.
 */
export default function IndustryBreakdownPanel({ dim, bizDate, market = 'A+H' }) {
  const [data, setData] = useState(null)
  const [expandedKey, setExpandedKey] = useState(null)
  const [detail, setDetail] = useState(null)
  const [expandedMetric, setExpandedMetric] = useState(null)
  const [metricWindow, setMetricWindow] = useState(90)

  useEffect(() => {
    if (!bizDate) return
    setExpandedKey(null)
    setExpandedMetric(null)
    getDimension(dim, bizDate, market).then(setData).catch(() => setData(null))
  }, [dim, bizDate, market])

  useEffect(() => {
    if (!expandedKey || !bizDate) {
      setDetail(null)
      return
    }
    getDimensionDetail(dim, expandedKey, bizDate, market).then(setDetail).catch(() => setDetail(null))
  }, [expandedKey, dim, bizDate, market])

  if (!bizDate) return <div className="empty">业务日期未就绪</div>
  if (!data) return <div className="empty">加载 {DIM_LABELS[dim]} 数据…</div>
  if (!data.portfolio || data.portfolio.length === 0) {
    return <div className="empty">本维度暂无数据（{DIM_LABELS[dim]}）</div>
  }

  const portfolioTotal = data.totals?.portfolio?.amount_cny || 1
  const csiTotal = data.totals?.csi300?.amount_cny || 100
  const csi300ByKey = Object.fromEntries(data.csi300.map(r => [r.key, r]))

  const toggleMetric = (metric) => {
    setExpandedMetric(prev => (prev === metric ? null : metric))
  }

  return (
    <div className="industry-breakdown-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span className="section-title">{DIM_LABELS[dim]} — {data.portfolio.length} 项</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"GeistMono",monospace' }}>
          总金额 {data.totals?.portfolio?.amount_cny?.toLocaleString() || '-'} CNY
        </span>
      </div>

      <div className="table-wrap" style={{ maxHeight: 500, overflowY: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>{DIM_LABELS[dim]}</th>
              <th style={{ textAlign: 'right' }}>只数</th>
              <th style={{ textAlign: 'right' }}>金额(CNY)</th>
              <th style={{ textAlign: 'right' }}>权重%</th>
              <th colSpan={3} style={{ textAlign: 'center', cursor: 'pointer' }} onClick={() => toggleMetric('pe_weighted')}>
                {expandedMetric === 'pe_weighted' ? '▼ ' : ''}组合 PE / PB / PS
              </th>
              <th colSpan={3} style={{ textAlign: 'center', cursor: 'pointer' }} onClick={() => toggleMetric('csi300_pe')}>
                {expandedMetric === 'csi300_pe' ? '▼ ' : ''}CSI300 PE / PB / PS
              </th>
            </tr>
            <tr>
              <th></th>
              <th></th>
              <th></th>
              <th></th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleMetric('pe_weighted')}>PE</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleMetric('pb_weighted')}>PB</th>
              <th style={{ textAlign: 'right', cursor: 'pointer' }} onClick={() => toggleMetric('ps_weighted')}>PS</th>
              <th style={{ textAlign: 'right' }}>PE</th>
              <th style={{ textAlign: 'right' }}>PB</th>
              <th style={{ textAlign: 'right' }}>PS</th>
            </tr>
          </thead>
          <tbody>
            {data.portfolio.map(row => {
              const csi = csi300ByKey[row.key]
              const isOpen = expandedKey === row.key
              return (
                <React.Fragment key={row.key}>
                  <tr
                    className={isOpen ? 'expanded' : ''}
                    style={{ cursor: 'pointer', background: isOpen ? 'var(--bg-soft, rgba(255,255,255,0.04))' : undefined }}
                    onClick={() => setExpandedKey(prev => prev === row.key ? null : row.key)}
                  >
                    <td title={row.key} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {row.key}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{row.stock_count}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', fontWeight: 600 }}>
                      {row.amount_cny.toLocaleString()}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>
                      {row.weight_pct.toFixed(2)}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{row.pe_weighted?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{row.pb_weighted?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{row.ps_weighted?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>{csi?.pe_weighted?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>{csi?.pb_weighted?.toFixed(2) ?? '-'}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>{csi?.ps_weighted?.toFixed(2) ?? '-'}</td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={10} style={{ padding: 0, background: 'rgba(0,0,0,0.15)' }}>
                        <IndustryDrilldownTable
                          dim={dim}
                          keyName={row.key}
                          detail={detail}
                          onMetricToggle={(metric) => toggleMetric(metric)}
                          expandedMetric={expandedMetric}
                        />
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
                {data.totals?.portfolio?.amount_cny?.toLocaleString() || '-'}
              </td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>100.00</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{data.totals?.portfolio?.pe_weighted?.toFixed(2) || '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{data.totals?.portfolio?.pb_weighted?.toFixed(2) || '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{data.totals?.portfolio?.ps_weighted?.toFixed(2) || '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>{data.totals?.csi300?.pe_weighted?.toFixed(2) || '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>{data.totals?.csi300?.pb_weighted?.toFixed(2) || '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', color: 'var(--text-secondary)' }}>{data.totals?.csi300?.ps_weighted?.toFixed(2) || '-'}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {expandedMetric && (
        <div className="metric-timeseries">
          <div className="metric-timeseries-header">
            <span className="mt-title">
              {expandedMetric === 'pe_weighted' ? 'PE' :
               expandedMetric === 'pb_weighted' ? 'PB' :
               expandedMetric === 'ps_weighted' ? 'PS' : 'CSI300 PE'} 序时变化
            </span>
            <select value={metricWindow} onChange={e => setMetricWindow(Number(e.target.value))}>
              <option value={90}>近 90 天</option>
              <option value={180}>近 180 天</option>
              <option value={360}>近 360 天</option>
            </select>
            <button className="btn-ghost" onClick={() => setExpandedMetric(null)}>折叠</button>
          </div>
          <MetricTimeseriesChart
            metric={expandedMetric === 'csi300_pe' ? 'pe_weighted' : expandedMetric}
            window={metricWindow}
            scope="both"
          />
        </div>
      )}
    </div>
  )
}