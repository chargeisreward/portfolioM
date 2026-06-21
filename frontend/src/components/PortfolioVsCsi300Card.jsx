import React, { useEffect, useState } from 'react'
import { getFullHoldingSummary } from '../api'

const fmtNum = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d))
const fmtAmount = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))
const fmtPctRaw = (v, d = 2) => (v == null ? '-' : (v * 100).toFixed(d) + '%')

// 推断原币种 (与 backend guess_currency_from_code 对齐)
const US_CODES = new Set(['GOOGL','NVDA','INTC','SNDK','AMD','AAPL','MSFT','AMZN','TSLA','QQQ'])
function inferCurrency(code) {
  if (!code) return 'CNY'
  const c = String(code).toUpperCase()
  if (US_CODES.has(c)) return 'USD'
  if (c.endsWith('.HK') || (/^\d{5}$/.test(c))) return 'HKD'
  return 'CNY'
}

/**
 * PortfolioVsCsi300Card — 全持仓 4 口径估值对比 (虚拟盈利法 · 以最新收盘价为口径).
 *
 * 4 个口径:
 *   - 全部下钻证券: drilled 全部 (12 个下钻指数聚合)
 *   - 全部 A 股证券: drilled 中 A 股
 *   - 全部港股证券: drilled 中港股
 *   - 全部沪深 300 证券: CSI 300 指数 (仅作指标参照系, 不含金额 / 占比)
 *
 * 算法 (3 张卡片):
 *   amount = est_market_value_cny (current price × shares, 前端做 CNY 折算)
 *   pe_per_stock = pe_ttm × (current/baseline)
 *   virt_pe = Σ (amount / pe_per_stock)
 *   weighted_pe = Σ amount / virt_pe
 *
 * 占比 (3 张卡片) = card 金额 (CNY) / 表格 估算市值合计 (CNY)
 * CSI 300 不显示金额 / 占比 (按用户口径).
 *
 * 布局: 2×2 网格, 颜色应用在标题 + 数字 (PE/PB/PS/DY/金额/占比),
 * 卡片颜色与对应列高亮一致.
 */
const SCOPES = [
  { key: 'drilled', label: '全部下钻证券',     color: '#ff5252' },  // 红
  { key: 'a_only',  label: '全部 A 股证券',   color: '#4fc3f7' },  // 蓝
  { key: 'h_only',  label: '全部港股证券',     color: '#00e676' },  // 绿
  { key: 'csi300',  label: '全部沪深 300 证券', color: '#ffd54f' }, // 黄
]

export default function PortfolioVsCsi300Card({ bizDate, totalEstCNY = 0 }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!bizDate) return
    setLoading(true)
    import('../api').then(api => {
      api.getFullHoldingSummary(bizDate)
        .then(d => { setData(d); setLoading(false) })
        .catch(e => { setErr(e?.message || 'load failed'); setLoading(false) })
    })
  }, [bizDate])

  if (err) return <div className="scope-card scope-error">加载全持仓 4 口径对比失败: {err}</div>
  if (loading) return <div className="scope-card">加载全持仓 4 口径对比…</div>
  if (!data) return null

  return (
    <div className="scope-card">
      <div className="scope-header">
        <span className="scope-title">全持仓 4 口径估值对比 (虚拟盈利法 · 最新收盘价)</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          amount = 估算市值 (CNY)  ·  PE = current/baseline 缩放  ·  占比 = card 金额 / 表格 估算市值合计
        </span>
      </div>
      <div className="drill-grid drill-grid-2x2">
        {SCOPES.map(s => {
          const d = data[s.key] || {}
          const color = s.color
          const isCsi = s.key === 'csi300'
          const amountCNY = isCsi ? null : (d.total_amount_cny ?? 0)
          const ratioPct = (!isCsi && totalEstCNY > 0)
            ? (amountCNY / totalEstCNY)
            : null
          return (
            <div key={s.key} className="drill-card">
              <div className="drill-card-header">
                <div className="drill-card-title-row">
                  <span className="drill-fund-name" style={{ color, fontWeight: 600 }}>{s.label}</span>
                </div>
              </div>
              <div className="drill-card-stats">
                <div className="drill-stat"><span className="lbl">PE</span><span className="val" style={{ color, fontWeight: 600 }}>{fmtNum(d.weighted_pe, 1)}</span></div>
                <div className="drill-stat"><span className="lbl">PB</span><span className="val" style={{ color, fontWeight: 600 }}>{fmtNum(d.weighted_pb, 1)}</span></div>
                <div className="drill-stat"><span className="lbl">PS</span><span className="val" style={{ color, fontWeight: 600 }}>{fmtNum(d.weighted_ps, 1)}</span></div>
                <div className="drill-stat"><span className="lbl">股息率%</span><span className="val" style={{ color, fontWeight: 600 }}>{fmtNum(d.weighted_dividend_yield, 1)}</span></div>
                <div className="drill-stat"><span className="lbl">股票数</span><span className="val" style={{ color, fontWeight: 600 }}>{d.stock_count ?? '-'}</span></div>
                {!isCsi && (
                  <>
                    <div className="drill-stat"><span className="lbl">金额(CNY)</span><span className="val" style={{ color, fontWeight: 600 }}>{fmtAmount(amountCNY)}</span></div>
                    <div className="drill-stat"><span className="lbl">占比%</span><span className="val" style={{ color, fontWeight: 600 }}>{fmtPctRaw(ratioPct, 1)}</span></div>
                    <div className="drill-stat drill-stat-secondary"><span className="lbl">偏差%</span><span className="val">-</span></div>
                  </>
                )}
                {isCsi && (
                  <>
                    <div className="drill-stat drill-stat-secondary"><span className="lbl">金额(CNY)</span><span className="val">参照系</span></div>
                    <div className="drill-stat drill-stat-secondary"><span className="lbl">占比%</span><span className="val">参照系</span></div>
                    <div className="drill-stat drill-stat-secondary"><span className="lbl">偏差%</span><span className="val">参照系</span></div>
                  </>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}