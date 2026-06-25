import React, { useEffect, useState } from 'react'
import { getDataVersion } from '../api'

const fmtNum = (v, d = 1) => (v == null ? '-' : Number(v).toFixed(d))
const fmtPct = (v, d = 1) => (v == null ? '-' : Number(v).toFixed(d))
const fmtAmount = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))

/**
 * DrillableIndicesPage (下钻 sub-page).
 *
 * 卡片以"指数"为单位：多个跟踪同一指数的基金持仓合并后展示。
 * 卡片显示 8 项指标 (4 行 × 2 列):
 *   Row 1: PE | PB
 *   Row 2: PS | 股息率%
 *   Row 3: 股票数 | 金额(CNY)
 *   Row 4: 占比% | 估算偏差%
 *
 * 点击卡片 → 展开下钻明细（来自所有跟踪该指数的基金底层股票合并）。
 */
export default function DrillableFundsPage() {
  const [indices, setIndices] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [dataVer, setDataVer] = useState(null)

  // 下钻用当日日期：fund_drill_snapshot 由 scheduler 每日生成，
  // service 层会自动回退到 ≤ today 的最新 snapshot。
  // 不使用 current_business_date（那是指数权重的月度更新日期，与下钻无关）。
  const today = new Date().toLocaleDateString('sv-SE')  // "YYYY-MM-DD" 本地时区

  useEffect(() => {
    getDataVersion().then(setDataVer).catch(() => setDataVer(null))
  }, [])

  useEffect(() => {
    setLoading(true)
    import('../api').then(api => {
      api.getDrillableIndices(today)
        .then(d => { setIndices(d?.indices || []); setLoading(false); })
        .catch(e => { setErr(e?.message || 'load failed'); setLoading(false); })
    })
  }, [today])

  const toggle = (idxCode) => {
    if (expanded === idxCode) {
      setExpanded(null)
      setDetail(null)
      return
    }
    setExpanded(idxCode)
    setDetail(null)
    setDetailLoading(true)
    import('../api').then(api => {
      api.getIndexDrill(idxCode, today)
        .then(d => { setDetail(d); setDetailLoading(false); })
        .catch(e => { setErr(e?.message); setDetailLoading(false); })
    })
  }

  if (loading) return <div className="empty">加载可下钻指数…</div>
  if (err) return <div className="empty">加载失败: {err}</div>
  if (!indices.length) return <div className="empty">无可下钻指数</div>

  // === 排名着色 (PE/PB/PS: 高=红; 股息率: 高=绿; 占比: 统一黄) ===
  // 暗色背景下需要高亮色 — 避免"脏色"
  //   PE/PB/PS:  深红 -> 浅红 -> 极浅红 -> 浅绿 -> 中绿 -> 深绿
  //              (深=数值最大; 深绿=数值最小; null 不参与)
  //   股息率:    反过来 — 深绿=最大, 深红=最小
  const PALETTE_HIGH_TO_LOW = [
    '#ff5252',   // 1 (max) — 鲜红
    '#ff8a80',   // 2       — 中红
    '#ffcdd2',   // 3       — 浅粉
    '#b9f6ca',   // n-2     — 浅绿
    '#69f0ae',   // n-1     — 中绿
    '#00e676',   // n (min) — 鲜绿
  ]
  //              ranks 1 (max), 2, 3, n-2, n-1, n (min)

  function rankColor(values, v, palette) {
    if (v == null || values.length === 0) return undefined
    const sorted = [...values].sort((a, b) => b - a)  // descending
    const rank = sorted.indexOf(v)  // 0-based; rank 0 = max
    const n = sorted.length
    if (n >= 6) {
      // top-3 red, bottom-3 green (or reversed for DY)
      if (rank < 3) return palette[rank]                // max, 2nd, 3rd
      if (rank >= n - 3) return palette[5 - (n - 1 - rank)]  // min, 2nd-min, 3rd-min
      return undefined
    }
    // n < 6: 用全部色阶
    const slice = palette.slice(0, n)
    return slice[rank]
  }

  const pes = indices.map(c => c.weighted_pe).filter(v => v != null)
  const pbs = indices.map(c => c.weighted_pb).filter(v => v != null)
  const pss = indices.map(c => c.weighted_ps).filter(v => v != null)
  const dys = indices.map(c => c.weighted_dividend_yield).filter(v => v != null)

  const colorOf = (card, field) => {
    if (field === 'pe') return rankColor(pes, card.weighted_pe, PALETTE_HIGH_TO_LOW)
    if (field === 'pb') return rankColor(pbs, card.weighted_pb, PALETTE_HIGH_TO_LOW)
    if (field === 'ps') return rankColor(pss, card.weighted_ps, PALETTE_HIGH_TO_LOW)
    if (field === 'dy') return rankColor(dys, card.weighted_dividend_yield, [...PALETTE_HIGH_TO_LOW].reverse())
    return undefined
  }

  return (
    <div className="drill-page">
      <div className="drill-header">
        <span className="drill-title">可下钻指数 — {indices.length} 个</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          按组合占比降序 · 多个跟踪同指数的基金已合并 · 点击卡片展开下钻
        </span>
      </div>
      {dataVer && (
        <div className="drill-snapshot-bar">
          <span className="dsb-label">数据快照</span>
          <span className="dsb-item">
            <span className="dsb-key">财务/构成</span>
            <span className="dsb-val">{dataVer.current_business_date || '—'}</span>
          </span>
          <span className="dsb-sep" />
          <span className="dsb-item">
            <span className="dsb-key">A股价</span>
            <span className="dsb-val">{dataVer.price_dates?.CN || '—'}</span>
          </span>
          <span className="dsb-item">
            <span className="dsb-key">港股价</span>
            <span className="dsb-val">{dataVer.price_dates?.HK || '—'}</span>
          </span>
          <span className="dsb-item">
            <span className="dsb-key">美股价</span>
            <span className="dsb-val">{dataVer.price_dates?.US || '—'}</span>
          </span>
        </div>
      )}
      <div className="drill-grid">
        {indices.map(card => {
          const isOpen = expanded === card.index_code
          const dev = card.est_deviation_pct
          const devColor = dev > 0 ? 'var(--chart-up)' : dev < 0 ? 'var(--chart-down)' : 'var(--text-secondary)'
          return (
            <div key={card.index_code} className={`drill-card ${isOpen ? 'drill-card-open' : ''}`}>
              <div className="drill-card-header" onClick={() => toggle(card.index_code)}>
                <div className="drill-card-title-row">
                  <span className="drill-fund-code">{card.index_code}</span>
                  <span className="drill-fund-name">{card.index_name || card.index_code}</span>
                  <span className="drill-card-toggle">{isOpen ? '▼' : '▸'}</span>
                </div>
                <div className="drill-card-meta">
                  <span style={{ marginRight: 10 }}>
                    <span style={{ color: 'var(--text-muted)' }}>跟踪基金</span>
                    <span style={{ color: 'var(--accent)', fontWeight: 600, marginLeft: 4 }}>{(card.fund_codes || []).length}</span>
                    <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>
                      {(card.fund_codes || []).join(' · ')}
                    </span>
                  </span>
                  <span>
                    <span style={{ color: 'var(--text-muted)' }}>股票数</span>
                    <span style={{ color: 'var(--text-secondary)', fontWeight: 600, marginLeft: 4 }}>{card.stock_count}</span>
                  </span>
                </div>
              </div>
              <div className="drill-card-stats">
                <div className="drill-stat"><span className="lbl">PE</span><span className="val" style={{ color: colorOf(card, 'pe'), fontWeight: colorOf(card, 'pe') ? 600 : 400 }}>{fmtNum(card.weighted_pe)}</span></div>
                <div className="drill-stat"><span className="lbl">PB</span><span className="val" style={{ color: colorOf(card, 'pb'), fontWeight: colorOf(card, 'pb') ? 600 : 400 }}>{fmtNum(card.weighted_pb)}</span></div>
                <div className="drill-stat"><span className="lbl">PS</span><span className="val" style={{ color: colorOf(card, 'ps'), fontWeight: colorOf(card, 'ps') ? 600 : 400 }}>{fmtNum(card.weighted_ps)}</span></div>
                <div className="drill-stat"><span className="lbl">股息率%</span><span className="val" style={{ color: colorOf(card, 'dy'), fontWeight: colorOf(card, 'dy') ? 600 : 400 }}>{fmtNum(card.weighted_dividend_yield)}</span></div>
                <div className="drill-stat drill-stat-secondary"><span className="lbl">股票数</span><span className="val">{card.stock_count}</span></div>
                <div className="drill-stat drill-stat-secondary"><span className="lbl">金额(CNY)</span><span className="val">{fmtAmount(card.static_amount_cny)}</span></div>
                <div className="drill-stat drill-stat-secondary"><span className="lbl">占比%</span><span className="val" style={{ color: '#ffd54f', fontWeight: 600 }}>{fmtPct(card.weight_pct)}</span></div>
                <div className="drill-stat drill-stat-secondary"><span className="lbl">偏差%</span>
                  <span className="val" style={{ color: devColor, fontWeight: 600 }}>
                    {dev != null && dev !== 0 ? (dev > 0 ? '+' : '') + dev.toFixed(2) + '%' : '-'}
                  </span>
                </div>
              </div>

              {isOpen && (
                <div className="drill-detail">
                  {detailLoading ? (
                    <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>展开中…</div>
                  ) : detail?.error ? (
                    <div className="empty">{detail.error}</div>
                  ) : detail?.constituents ? (
                    <>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
                        下钻明细：共 {detail.constituents.filter(r => !r.is_cash).length} 只股票 + 现金
                      </div>
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>代码</th>
                            <th>名称</th>
                            <th style={{ textAlign: 'right' }}>权重%</th>
                            <th style={{ textAlign: 'right' }}>约当数量</th>
                            <th style={{ textAlign: 'right' }}>昨日收盘·原币</th>
                            <th style={{ textAlign: 'right' }}>昨日收盘·本币</th>
                            <th style={{ textAlign: 'right' }}>PE</th>
                            <th style={{ textAlign: 'right' }}>PB</th>
                            <th style={{ textAlign: 'right' }}>PS</th>
                            <th style={{ textAlign: 'right' }}>股息率%</th>
                            <th style={{ textAlign: 'right' }}>估算市值</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.constituents.map((r, i) => (
                            <tr key={r.stock_code + i} style={r.is_cash ? { background: 'var(--bg-raised)', fontStyle: 'italic' } : undefined}>
                              <td>{r.stock_code}</td>
                              <td>{r.stock_name}</td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtPct(r.weight_at_baseline_pct)}</td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                                {r.shares_equivalent != null ? Math.round(r.shares_equivalent).toLocaleString() : '-'}
                              </td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                                {r.current_price != null ? r.current_price.toFixed(2) : '-'}
                              </td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                                {r.current_price_cny != null ? r.current_price_cny.toFixed(2) : '-'}
                              </td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.pe_ttm_dynamic ?? r.pe_ttm)}</td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.pb_mrq_dynamic ?? r.pb_mrq)}</td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.ps_ttm_dynamic ?? r.ps_ttm)}</td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.dividend_yield)}</td>
                              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtAmount(r.est_market_value_cny)}</td>
                            </tr>
                          ))}
                        </tbody>
                        <tfoot>
                          <tr style={{ fontWeight: 600, borderTop: '1px solid var(--border-strong)' }}>
                            <td colSpan={2} style={{ color: 'var(--text-muted)', fontSize: 11 }}>合计 · {detail.constituents.filter(r => !r.is_cash).length} 只股票 + 现金</td>
                            <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>100.00</td>
                            <td colSpan={7}></td>
                            <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                              {fmtAmount(detail.constituents.reduce((s, r) => s + (r.est_market_value_cny || 0), 0))}
                            </td>
                          </tr>
                        </tfoot>
                      </table>
                    </>
                  ) : null}
                  <div style={{ marginTop: 8, textAlign: 'right' }}>
                    <button className="btn-ghost" onClick={() => toggle(card.index_code)} style={{ fontSize: 11 }}>折叠</button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}