import React, { useEffect, useMemo, useState } from 'react'
import { getFullHoldingTable, getLatestExchangeRates } from '../api'

const fmtAmount = (v) => {
  if (v == null) return '-'
  return Math.round(v).toLocaleString('en-US')
}
const fmtPct = (v) => (v == null ? '-' : (Number(v).toFixed(2) + '%'))
const fmtNum = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d))
const fmtShares = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))

// 从证券代码推断原币种 (与 backend crawlers/exchange_rates.py:guess_currency_from_code 对齐)
const US_CODES = new Set(['GOOGL', 'NVDA', 'INTC', 'SNDK', 'AMD', 'AAPL', 'MSFT', 'AMZN', 'TSLA', 'QQQ'])
function inferCurrency(code) {
  if (!code) return 'CNY'
  const c = String(code).toUpperCase()
  if (US_CODES.has(c)) return 'USD'
  if (c.endsWith('.HK') || (/^\d{5}$/.test(c))) return 'HKD'
  return 'CNY'  // .SH, .SZ, .OF, 6-digit
}

/**
 * FullHoldingTable — 全持仓 (drill 视角重排, Analysis → 全持仓 子页面).
 *
 * 数据来源:
 *   - full-holding-table: 后端一次返回未下钻部分 + 下钻成分股聚合 (避免重复下钻)
 *   - exchange-rates/latest: 原币种 (HKD/USD) → CNY 汇率
 *
 * 排版规则:
 *   - 上半部 "未下钻": 直接持股 + 未下钻基金 + 现金
 *   - 灰色分割线
 *   - 下半部 "下钻": 可下钻指数成分股 (drilled_fund 已被替换为成分股)
 *   - 各段内部按 权重% 降序; 同一股票跨多指数合并
 *
 * 字段语义:
 *   - 约当数量: 直股 = amount_cny / baseline_price (实际持股数);
 *               成分股 = Σ amount_529 / baseline_price (drill 等效股数);
 *               基金/现金 = '-' (后端不返回)
 *   - 昨日收盘: 后端 current_price (原币种) → 一律折算为人民币展示
 *   - 估算市值: 同样折算为人民币
 *   - 基金/现金的 PE/PB/PS/股息率 = '-' (不适用)
 *
 * 数量与 Overview 一致 (同代码合并; source_type ∈ {direct_stock, undrilled_fund, cash})
 */
export default function FullHoldingTable({ bizDate, onTotalEstChange }) {
  const [undrilledHoldings, setUndrilledHoldings] = useState([])
  const [drilledStocks, setDrilledStocks] = useState({})
  const [fxRates, setFxRates] = useState({ CNY: 1.0 })  // {USD: 7.18, HKD: 0.92, CNY: 1}
  const [loading, setLoading] = useState(true)
  const [viewFilter, setViewFilter] = useState('all')

  useEffect(() => {
    getLatestExchangeRates()
      .then(rates => {
        // 后端返回 {USD: {date, rate, source}, ...}，前端需要纯数字汇率
        const numeric = { CNY: 1.0 }
        for (const [cur, meta] of Object.entries(rates || {})) {
          if (meta && typeof meta === 'object' && meta.rate != null) {
            numeric[cur] = meta.rate
          } else if (typeof meta === 'number') {
            numeric[cur] = meta
          } else {
            numeric[cur] = null
          }
        }
        setFxRates(numeric)
      })
      .catch(() => setFxRates({ CNY: 1.0 }))
  }, [])

  useEffect(() => {
    if (!bizDate) return
    setLoading(true)
    getFullHoldingTable(bizDate)
      .then((resp) => {
        setUndrilledHoldings(resp?.undrilled || [])
        setDrilledStocks(resp?.drilled || {})
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [bizDate])

  // 原币种 → CNY. 若汇率缺失返回 null (前端显示 '-')
  const toCNY = (amount, fromCurrency) => {
    if (amount == null) return null
    if (fromCurrency === 'CNY') return amount
    const rate = fxRates[fromCurrency]
    if (rate == null) return null
    return amount * rate
  }

  const mergedRows = useMemo(() => {
    const byCode = {}
    const ensure = (code) => {
      if (!byCode[code]) {
        byCode[code] = {
          stock_code: code,
          stock_name: null,
          source_type: null,
          amount_cny: 0,
          shares: null,
          current_price: null,
          pe_ttm: null,
          pb_mrq: null,
          ps_ttm: null,
          dividend_yield: null,
          est_market_value_cny: 0,
          is_drill: false,
          is_cash: false,
        }
      }
      return byCode[code]
    }

    // 未下钻: 直接持股 + 未下钻基金 + 现金
    for (const r of undrilledHoldings) {
      const acc = ensure(r.stock_code)
      acc.source_type = r.source_type
      acc.stock_name = acc.stock_name || r.stock_name
      acc.amount_cny += (r.amount_cny || 0)
      if (r.shares != null) acc.shares = (acc.shares || 0) + r.shares

      const cur = inferCurrency(r.stock_code)
      // 估算市值: 统一用后端 est_market_value_cny (后端已为 undrilled_fund 算 quantity × NAV)
      //            经汇率折算为 CNY; 现金 row est=null, 回退 amount_cny
      if (r.est_market_value_cny != null) {
        const ev = toCNY(r.est_market_value_cny, cur)
        if (ev != null) acc.est_market_value_cny += ev
        else acc.est_market_value_cny += (r.amount_cny || 0)
      } else {
        acc.est_market_value_cny += (r.amount_cny || 0)
      }
      // 价格/估值: 后端返回原币种, 折算为 CNY
      if (r.current_price != null && acc.current_price == null) {
        acc.current_price = toCNY(r.current_price, cur)
      }
      if (r.pe_ttm_dynamic != null && acc.pe_ttm == null) acc.pe_ttm = r.pe_ttm_dynamic
      if (r.pb_mrq_dynamic != null && acc.pb_mrq == null) acc.pb_mrq = r.pb_mrq_dynamic
      if (r.ps_ttm_dynamic != null && acc.ps_ttm == null) acc.ps_ttm = r.ps_ttm_dynamic
      if (r.dividend_yield != null && acc.dividend_yield == null) acc.dividend_yield = r.dividend_yield
    }

    // 可下钻成分股 (drill 算法同下钻页面) + 下钻-现金（5% 现金部分）
    for (const code of Object.keys(drilledStocks)) {
      const s = drilledStocks[code]
      const isCash = s.is_cash === true || code === 'CASH'
      const acc = ensure(code)
      acc.source_type = 'drilled'
      acc.is_drill = true
      acc.is_cash = isCash
      acc.stock_name = acc.stock_name || s.stock_name
      // 现金行无约当数量，不累加 shares
      if (!isCash) {
        acc.shares = (acc.shares || 0) + (s.shares_equivalent || 0)
      }

      const cur = inferCurrency(code)
      // 双币种规则 (2026-06-25)：后端 drilled 段已返回本币(CNY)字段：
      //   est_market_value_cny = shares × current_price_cny（本币 CNY，不再 toCNY 双重折算）
      //   current_price_cny    = 原币价 × fx_rate（本币 CNY）
      // 优先用本币字段，fallback 到原币 × toCNY 折算（兼容旧后端）
      if (s.est_market_value_cny != null) {
        acc.est_market_value_cny += s.est_market_value_cny
      }
      if (acc.current_price == null) {
        acc.current_price = (s.current_price_cny != null)
          ? s.current_price_cny
          : toCNY(s.current_price, cur)
      }
      // 估值字段：优先用动态值（基于最新收盘价调整），fallback 到基准日值
      const peV = s.pe_ttm_dynamic ?? s.pe_ttm
      const pbV = s.pb_mrq_dynamic ?? s.pb_mrq
      const psV = s.ps_ttm_dynamic ?? s.ps_ttm
      if (peV != null && acc.pe_ttm == null) acc.pe_ttm = peV
      if (pbV != null && acc.pb_mrq == null) acc.pb_mrq = pbV
      if (psV != null && acc.ps_ttm == null) acc.ps_ttm = psV
      if (s.dividend_yield != null && acc.dividend_yield == null) acc.dividend_yield = s.dividend_yield
    }

    const rows = Object.values(byCode)
    const totalEst = rows.reduce((s, r) => s + (r.est_market_value_cny || 0), 0)
    for (const r of rows) {
      r.weight_pct = totalEst > 0 ? ((r.est_market_value_cny || 0) / totalEst * 100) : 0
    }

    const undrilled = rows.filter(r => !r.is_drill).sort((a, b) => b.weight_pct - a.weight_pct)
    const drilled = rows.filter(r => r.is_drill).sort((a, b) => b.weight_pct - a.weight_pct)

    return { undrilled, drilled, totalEst }
  }, [undrilledHoldings, drilledStocks, fxRates])

  const { undrilled, drilled, totalEst } = mergedRows

  // 暴露 估算市值合計 给 4 卡片 用于占比计算
  useEffect(() => {
    if (onTotalEstChange) onTotalEstChange(totalEst)
  }, [totalEst, onTotalEstChange])

  if (!bizDate) return <div className="empty">业务日期未就绪</div>
  if (loading) return <div className="empty">加载全持仓数据…</div>

  const totalCount = undrilled.length + drilled.length
  const filters = [
    { key: 'all', label: '全持仓', count: totalCount },
    { key: 'undrilled', label: '未下钻', count: undrilled.length },
    { key: 'drilled', label: '下钻', count: drilled.length },
  ]

  const showUndrilled = viewFilter === 'all' || viewFilter === 'undrilled'
  const showDrilled = viewFilter === 'all' || viewFilter === 'drilled'
  const showSeparator = showUndrilled && showDrilled && undrilled.length > 0 && drilled.length > 0

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{
          display: 'inline-flex', gap: 0, alignItems: 'center',
          border: '1px solid rgba(255,255,255,0.55)',
          borderRadius: 6, padding: '2px',
        }}>
          {filters.map(f => {
            const active = viewFilter === f.key
            return (
              <button key={f.key} onClick={() => setViewFilter(f.key)} style={{
                padding: '4px 14px', border: 'none', borderRadius: 4,
                background: active ? 'var(--accent)' : 'transparent',
                color: active ? '#fff' : 'var(--text-secondary)',
                cursor: 'pointer', fontSize: 12, fontWeight: active ? 600 : 400,
                transition: 'all 0.15s',
              }}>
                {f.label} {f.count}
              </button>
            )
          })}
        </div>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          估算市值合计 {fmtAmount(totalEst)} CNY
        </span>
      </div>

      <div className="table-wrap" style={{ maxHeight: 700, overflowY: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 90 }}>代码</th>
              <th>名称</th>
              <th style={{ textAlign: 'center', width: 60 }}>下钻</th>
              <th style={{ textAlign: 'right', width: 70 }}>权重%</th>
              <th style={{ textAlign: 'right', width: 110 }}>约当数量</th>
              <th style={{ textAlign: 'right', width: 90 }}>昨日收盘</th>
              <th style={{ textAlign: 'right', width: 70 }}>PE</th>
              <th style={{ textAlign: 'right', width: 70 }}>PB</th>
              <th style={{ textAlign: 'right', width: 70 }}>PS</th>
              <th style={{ textAlign: 'right', width: 80 }}>股息率%</th>
              <th style={{ textAlign: 'right', width: 120 }}>估算市值</th>
            </tr>
          </thead>
          <tbody>
            {showUndrilled && undrilled.map(r => (
              <tr key={`u-${r.stock_code}`}>
                <td style={{ fontFamily: '"GeistMono",monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.stock_code}</td>
                <td style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.stock_name || '-'}</td>
                <td style={{ textAlign: 'center', color: '#4fc3f7' }}>◻</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtPct(r.weight_pct)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtShares(r.shares)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.current_price)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.pe_ttm)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.pb_mrq)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.ps_ttm)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.dividend_yield)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtAmount(r.est_market_value_cny)}</td>
              </tr>
            ))}

            {showSeparator && (
              <tr>
                <td colSpan={11} style={{
                  height: 1, padding: 0,
                  background: 'var(--border-strong, #888)',
                  borderTop: '1px dashed var(--border-strong, #888)',
                  borderBottom: '1px dashed var(--border-strong, #888)',
                }} />
              </tr>
            )}

            {showDrilled && drilled.map(r => (
              <tr key={`d-${r.stock_code}`} style={r.is_cash ? { background: 'var(--bg-raised)', fontStyle: 'italic' } : undefined}>
                <td style={{ fontFamily: '"GeistMono",monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.stock_code}</td>
                <td style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.stock_name || '-'}</td>
                <td style={{ textAlign: 'center', color: r.is_cash ? 'var(--text-muted)' : '#69f0ae', fontWeight: 600 }}>{r.is_cash ? '💵' : '✓'}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtPct(r.weight_pct)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{r.is_cash ? '-' : fmtShares(r.shares)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.current_price)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.pe_ttm)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.pb_mrq)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.ps_ttm)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtNum(r.dividend_yield)}</td>
                <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{fmtAmount(r.est_market_value_cny)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
