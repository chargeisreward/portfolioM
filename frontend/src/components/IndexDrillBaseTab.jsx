import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

const fmtNum = (v, d = 1) => (v == null ? '-' : Number(v).toFixed(d))
const fmtAmount = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))

/**
 * 指数下钻基础数据 tab — 模拟基金（10000 份）卡片视图。
 *
 * 数据源：
 *   - 卡片列表：GET /admin/index-drill-base
 *   - 双日明细：GET /admin/index-drill-base-detail?fund_code=...
 *
 * 概念：
 *   - "模拟基金"：固定 95% 股票 + 5% 现金，假设持有 10000 份
 *   - 卡片金额 = nav × 10000
 *   - 占比/偏差列置空（模拟基金不计算占比）
 *
 * 卡片布局参考 DrillableFundsPage.jsx，复用 .drill-page / .drill-grid / .drill-card 样式。
 */
export default function IndexDrillBaseTab({ onMissingConstituents, onMissingIndexMapping }) {
  const [cards, setCards] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  const [asOf, setAsOf] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const res = await api.get('/admin/index-drill-base')
      setCards(res.data.cards || [])
      setAsOf(res.data.as_of)
    } catch (e) {
      console.error('加载指数下钻基础数据失败', e)
      setErr(e.response?.data?.detail || e.message)
      setCards([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const toggle = async (fundCode, hasConstituents) => {
    // 缺失分支：根据 index_code 是否为空区分两种状态
    if (!hasConstituents) {
      const card = cards.find(c => c.fund_code === fundCode)
      const idx = card?.index_code
      if (!idx) {
        // 缺指数映射 → 切到基金-指数映射 tab
        onMissingIndexMapping?.(fundCode)
      } else {
        // 缺指数构成 → 跳到内容上传页（指数已预选）
        onMissingConstituents?.(idx)
      }
      return
    }

    if (expanded === fundCode) {
      setExpanded(null)
      setDetail(null)
      return
    }
    setExpanded(fundCode)
    setDetail(null)
    setDetailLoading(true)
    try {
      const res = await api.get('/admin/index-drill-base-detail', { params: { fund_code: fundCode } })
      setDetail(res.data)
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    } finally {
      setDetailLoading(false)
    }
  }

  if (loading) return <div className="empty">加载指数下钻基础数据…</div>
  if (err) return <div className="empty">加载失败: {err}</div>
  if (!cards.length) return <div className="empty">无可下钻基金</div>

  return (
    <div className="drill-page">
      <div className="drill-header">
        <span className="drill-title">指数下钻基础数据 — {cards.length} 只基金</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          模拟基金（每 10 万份） · as_of: {asOf || '—'} · 点击卡片展开明细
        </span>
      </div>
      <div className="drill-grid">
        {cards.map(card => (
          <DrillBaseCard
            key={card.fund_code}
            card={card}
            expanded={expanded === card.fund_code}
            detail={expanded === card.fund_code ? detail : null}
            detailLoading={expanded === card.fund_code && detailLoading}
            onToggle={() => toggle(card.fund_code, card.has_constituents)}
          />
        ))}
      </div>
    </div>
  )
}

/**
 * 单只基金的模拟下钻卡片。
 * 卡片本身显示 latest 字段的 8 项指标；点击展开双日并排表格。
 */
function DrillBaseCard({ card, expanded, detail, detailLoading, onToggle }) {
  // 缺指数映射（index_code 为空）→ 切到基金-指数映射 tab
  if (!card.has_constituents && !card.index_code) {
    return (
      <div className="drill-card drill-card-missing">
        <div className="drill-card-header" onClick={onToggle} style={{ cursor: 'pointer' }}>
          <div className="drill-card-title-row">
            <span className="drill-fund-code">{card.fund_code}</span>
            <span className="drill-fund-name">{card.fund_name || card.fund_code} · 每 10 万份</span>
            <span className="drill-card-toggle">⚠</span>
          </div>
          <div className="drill-card-meta">
            <span style={{ marginRight: 10 }}>
              <span style={{ color: 'var(--text-muted)' }}>指数</span>
              <span style={{ color: 'var(--text-secondary)', marginLeft: 4 }}>— 未设置</span>
            </span>
            <span style={{ color: 'var(--chart-down, #ff5252)', fontWeight: 600 }}>
              缺指数映射 · 点击设置
            </span>
          </div>
        </div>
      </div>
    )
  }

  // 缺指数构成（index_code 已设置但无 IndexConstituentSnapshot 数据）→ 跳内容上传页
  if (!card.has_constituents && card.index_code) {
    return (
      <div className="drill-card drill-card-missing">
        <div className="drill-card-header" onClick={onToggle} style={{ cursor: 'pointer' }}>
          <div className="drill-card-title-row">
            <span className="drill-fund-code">{card.fund_code}</span>
            <span className="drill-fund-name">{card.fund_name || card.fund_code} · 每 10 万份</span>
            <span className="drill-card-toggle">⚠</span>
          </div>
          <div className="drill-card-meta">
            <span style={{ marginRight: 10 }}>
              <span style={{ color: 'var(--text-muted)' }}>指数</span>
              <span style={{ color: 'var(--accent)', fontWeight: 600, marginLeft: 4 }}>{card.index_code}</span>
              <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{card.index_name || ''}</span>
            </span>
            <span style={{ color: 'var(--chart-down, #ff5252)', fontWeight: 600 }}>
              缺指数构成 · 点击上传
            </span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`drill-card ${expanded ? 'drill-card-open' : ''}`}>
      <div className="drill-card-header" onClick={onToggle} style={{ cursor: 'pointer' }}>
        <div className="drill-card-title-row">
          <span className="drill-fund-code">{card.fund_code}</span>
          <span className="drill-fund-name">{card.fund_name || card.fund_code}</span>
          <span className="drill-card-toggle">{expanded ? '▼' : '▸'}</span>
        </div>
        <div className="drill-card-subtitle">
          每 10 万份 · {card.nav_date || '—'}
        </div>
        <div className="drill-card-meta">
          <span>
            <span style={{ color: 'var(--text-muted)' }}>指数</span>
            <span style={{ color: 'var(--accent)', fontWeight: 600, marginLeft: 4 }}>
              {card.index_code || '—'}
            </span>
            <span style={{ color: 'var(--text-muted)', marginLeft: 4 }}>{card.index_name || ''}</span>
          </span>
          <span>
            <span style={{ color: 'var(--text-muted)' }}>股票数</span>
            <span style={{ color: 'var(--text-secondary)', fontWeight: 600, marginLeft: 4 }}>{card.stock_count ?? 0}</span>
          </span>
        </div>
      </div>
      <div className="drill-card-stats">
        <div className="drill-stat"><span className="lbl">PE</span><span className="val">{fmtNum(card.weighted_pe)}</span></div>
        <div className="drill-stat"><span className="lbl">PB</span><span className="val">{fmtNum(card.weighted_pb)}</span></div>
        <div className="drill-stat"><span className="lbl">PS</span><span className="val">{fmtNum(card.weighted_ps)}</span></div>
        <div className="drill-stat"><span className="lbl">股息率%</span><span className="val">{fmtNum(card.weighted_dividend_yield)}</span></div>
        <div className="drill-stat drill-stat-secondary"><span className="lbl">股票数</span><span className="val">{card.stock_count ?? 0}</span></div>
        <div className="drill-stat drill-stat-secondary"><span className="lbl">金额(CNY)</span><span className="val">{fmtAmount(card.per_10k_value)}</span></div>
        <div className="drill-stat drill-stat-secondary"><span className="lbl">占比%</span><span className="val" style={{ color: 'var(--text-muted)' }}>-</span></div>
        <div className="drill-stat drill-stat-secondary"><span className="lbl">偏差%</span><span className="val">{fmtNum(card.deviation_pct, 2)}</span></div>
      </div>

      {expanded && (
        <div className="drill-detail">
          {detailLoading ? (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>展开中…</div>
          ) : detail ? (
            <DrillBaseDetail detail={detail} />
          ) : null}
          <div style={{ marginTop: 8, textAlign: 'right' }}>
            <button className="btn-ghost" onClick={onToggle} style={{ fontSize: 11 }}>折叠</button>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * 双日并排明细表格。
 * 列：代码 | 名称 | 基期权重% | 基期约当数量 | 基期股价·原币 | 基期股价·本币 | 基期 PE | 基期 PB | 基期 PS | 基期股息率% | 基期估算市值
 *      ‖ 最新日权重% | 最新日约当数量 | 最新日股价·原币 | 最新日股价·本币 | 最新日 PE | 最新日 PB | 最新日 PS | 最新日股息率% | 最新日估算市值
 */
function DrillBaseDetail({ detail }) {
  const stocks = detail.stocks || []
  const stockRows = stocks.filter(s => s.stock_code !== 'CASH')
  const cashRow = stocks.find(s => s.stock_code === 'CASH')

  return (
    <>
      <div style={{ display: 'flex', gap: 16, marginBottom: 8, fontSize: 11, color: 'var(--text-muted)', flexWrap: 'wrap' }}>
        <span>基期: <strong style={{ color: 'var(--text-secondary)' }}>{detail.baseline_date || '—'}</strong></span>
        <span>基期 NAV: <strong style={{ color: 'var(--text-secondary)' }}>{fmtNum(detail.baseline_nav, 4)}</strong></span>
        <span>基期金额: <strong style={{ color: 'var(--text-secondary)' }}>{fmtAmount(detail.baseline_amount)}</strong></span>
        <span style={{ marginLeft: 16 }}>最新日: <strong style={{ color: 'var(--accent)' }}>{detail.latest_date || '—'}</strong></span>
        <span>最新日 NAV: <strong style={{ color: 'var(--accent)' }}>{fmtNum(detail.latest_nav, 4)}</strong></span>
        <span>最新日金额: <strong style={{ color: 'var(--accent)' }}>{fmtAmount(detail.latest_amount)}</strong></span>
      </div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6 }}>
        双日并排明细：共 {stockRows.length} 只股票 + 现金 · 约当数量 = 10000 × shares_equivalent
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="data-table" style={{ minWidth: 1200 }}>
          <thead>
            <tr>
              <th rowSpan="2">代码</th>
              <th rowSpan="2" style={{ minWidth: '7em' }}>名称</th>
              <th colSpan="9" style={{ textAlign: 'center', background: 'var(--bg-raised)' }}>基期 ({detail.baseline_date || '—'})</th>
              <th colSpan="9" style={{ textAlign: 'center', background: 'var(--bg-raised)' }}>最新日 ({detail.latest_date || '—'})</th>
            </tr>
            <tr>
              <th style={{ textAlign: 'right' }}>权重%·官方</th>
              <th style={{ textAlign: 'right' }}>约当数量</th>
              <th style={{ textAlign: 'right' }}>股价·原币</th>
              <th style={{ textAlign: 'right' }}>股价·本币</th>
              <th style={{ textAlign: 'right' }}>PE</th>
              <th style={{ textAlign: 'right' }}>PB</th>
              <th style={{ textAlign: 'right' }}>PS</th>
              <th style={{ textAlign: 'right' }}>股息率%</th>
              <th style={{ textAlign: 'right' }}>估算市值</th>
              <th style={{ textAlign: 'right' }}>权重%·实际</th>
              <th style={{ textAlign: 'right' }}>约当数量</th>
              <th style={{ textAlign: 'right' }}>股价·原币</th>
              <th style={{ textAlign: 'right' }}>股价·本币</th>
              <th style={{ textAlign: 'right' }}>PE</th>
              <th style={{ textAlign: 'right' }}>PB</th>
              <th style={{ textAlign: 'right' }}>PS</th>
              <th style={{ textAlign: 'right' }}>股息率%</th>
              <th style={{ textAlign: 'right' }}>估算市值</th>
            </tr>
          </thead>
          <tbody>
            {stockRows.map((s, i) => {
              const bw = s.baseline?.weight_pct
              const lw = s.latest?.effective_weight_pct ?? s.latest?.weight_pct
              const cmp = (bw != null && lw != null) ? (bw === lw ? 0 : (bw > lw ? 1 : -1)) : 0
              return (
                <tr key={s.stock_code + i}>
                  <td style={{ fontFamily: 'GeistMono, monospace' }}>{s.stock_code}</td>
                  <td>{s.stock_name}</td>
                  <DetailCells row={s.baseline} isBaseline={true} highlightWeight={cmp > 0} />
                  <DetailCells row={s.latest} isBaseline={false} highlightWeight={cmp < 0} />
                </tr>
              )
            })}
            {cashRow && (() => {
              const bw = cashRow.baseline?.weight_pct
              const lw = cashRow.latest?.effective_weight_pct ?? cashRow.latest?.weight_pct
              const cmp = (bw != null && lw != null) ? (bw === lw ? 0 : (bw > lw ? 1 : -1)) : 0
              return (
                <tr style={{ background: 'var(--bg-raised)', fontStyle: 'italic' }}>
                  <td>CASH</td>
                  <td>现金</td>
                  <DetailCells row={cashRow.baseline} isBaseline={true} highlightWeight={cmp > 0} />
                  <DetailCells row={cashRow.latest} isBaseline={false} highlightWeight={cmp < 0} />
                </tr>
              )
            })()}
          </tbody>
          <tfoot>
            <tr style={{ fontWeight: 600, borderTop: '1px solid var(--border-strong)' }}>
              <td colSpan="2" style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                合计 · {stockRows.length} 只股票 + 现金
              </td>
              {/* 基期 9 列（1 权重 + 7 中 + 1 估算市值）汇总 */}
              <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace', color: 'var(--accent)' }}>
                {fmtNum(detail.totals?.sum_weight_baseline, 2)}
              </td>
              <td colSpan="7"></td>
              <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>
                {fmtAmount(sumEstMv(stockRows, 'baseline'))}
              </td>
              {/* 最新日 9 列（1 权重 + 7 中 + 1 估算市值）汇总 */}
              <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace', color: 'var(--accent)' }}>
                {fmtNum(detail.totals?.sum_weight_latest, 2)}
              </td>
              <td colSpan="7"></td>
              <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>
                {fmtAmount(sumEstMv(stockRows, 'latest'))}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </>
  )
}

/** 渲染单日 9 列数据（权重/约当数量/股价原币/股价本币/PE/PB/PS/股息率/估算市值）。
 * isBaseline=true：基期，权重列显示 weight_pct（官方权重）
 * isBaseline=false：最新日，权重列显示 effective_weight_pct（实际权重，反映股价漂移）
 * highlightWeight=true：本日权重为两者中较大值，用红色加粗高亮（适合深色背景）
 */
function DetailCells({ row, isBaseline = true, highlightWeight = false }) {
  if (!row) {
    return (
      <>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
        <td style={{ textAlign: 'right' }}>-</td>
      </>
    )
  }
  // 基期显示官方权重，最新日显示实际权重（effective_weight_pct）
  const weightVal = isBaseline ? row.weight_pct : (row.effective_weight_pct ?? row.weight_pct)
  return (
    <>
      <td style={{
        textAlign: 'right',
        fontFamily: 'GeistMono, monospace',
        color: highlightWeight ? 'var(--chart-down, #ff5252)' : undefined,
        fontWeight: highlightWeight ? 700 : undefined,
      }}>{fmtNum(weightVal)}</td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>
        {row.user_shares != null ? Math.round(row.user_shares).toLocaleString() : '-'}
      </td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>{fmtNum(row.current_price, 2)}</td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>{fmtNum(row.current_price_cny, 2)}</td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>{fmtNum(row.pe_ttm)}</td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>{fmtNum(row.pb_mrq)}</td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>{fmtNum(row.ps_ttm)}</td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>{fmtNum(row.dividend_yield)}</td>
      <td style={{ textAlign: 'right', fontFamily: 'GeistMono, monospace' }}>{fmtAmount(row.est_market_value)}</td>
    </>
  )
}

/** 计算某日所有股票的估算市值合计。 */
function sumEstMv(stockRows, side) {
  return stockRows.reduce((s, r) => s + ((r[side] && r[side].est_market_value) || 0), 0)
}
