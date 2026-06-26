import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import * as echarts from 'echarts'
import * as api from '../api'
import { rawApi } from '../api'

const CAT_LABELS = { a_share_equity:'A股基金', a_share_etf:'A股ETF', bond:'债券', gold:'黄金', hk_equity:'港股', qdii_equity:'QDII', us_stock:'美股', us_etf:'美股ETF' }
const CAT_SHORT = { a_share_equity:'A基主动', a_share_etf:'A基指数', bond:'债券', gold:'黄金', hk_equity:'港股', qdii_equity:'QDI', us_stock:'美股', us_etf:'美股E' }
const TYPE2_LABELS = { dividend:'红利', emerging:'新兴产业', gold:'黄金' }

const fmtAmount = (v, symbol = '¥') => {
  if (v == null || v === 0) return '-'
  const n = Math.round(v)
  return symbol + n.toLocaleString('en-US')
}
const fmtQty = (v) => {
  if (v == null || v === 0) return '-'
  return Math.round(v).toLocaleString('en-US')
}
const fmtPct = (v) => {
  if (v == null || isNaN(v)) return '-'
  return (v * 100).toFixed(1) + '%'
}
const getCurrencySymbol = (c) => ({CNY:'¥', USD:'$', CAD:'C$', HKD:'HK$'})[c] || c+' '

export default function OverviewPanel() {
  const [summary, setSummary] = useState(null)
  const [allHoldings, setAllHoldings] = useState([])
  const [penTable, setPenTable] = useState([])
  const [pe, setPe] = useState({})
  const [growth, setGrowth] = useState({})
  const pieRef = useRef(null)
  const radarRef = useRef(null)
  const trendRef = useRef(null)
  const [trendData, setTrendData] = useState([])
  const [trendTotal, setTrendTotal] = useState(null)
  const [trendDays, setTrendDays] = useState(90)
  const [trendView, setTrendView] = useState('return')  // 'return' = 收益率%, 'value' = 资产净值
  const [trendReturn, setTrendReturn] = useState(null)  // {pct, abs} over the window
  const [sortKey, setSortKey] = useState('amount')
  const [sortDir, setSortDir] = useState('desc')
  const [currency, setCurrency] = useState('CNY')
  const [holdingsLocal, setHoldingsLocal] = useState([])
  const [typeFilter, setTypeFilter] = useState('all')
  const [type2Filter, setType2Filter] = useState('all')
  const [kpi, setKpi] = useState(null)        // spec §4.8: real KPI from /api/penetration/kpi
  const [bizDate, setBizDate] = useState(null) // for KPI fetch param
  const [marketIndices, setMarketIndices] = useState([])
  const [drillableCodes, setDrillableCodes] = useState(new Set())  // 可下钻基金代码集合

  // 获取可下钻基金代码列表（SecurityMaster.is_drillable=True）
  useEffect(() => {
    rawApi.get('/securities').then(r => {
      const codes = new Set((r.data || []).filter(s => s.is_drillable).map(s => s.security_code))
      setDrillableCodes(codes)
    }).catch(() => {})
  }, [])

  // 市场指数涨跌幅（30秒刷新）
  useEffect(() => {
    const fetchIndices = () => rawApi.get('/market/indices').then(r => setMarketIndices(r.data?.indices || [])).catch(() => {})
    fetchIndices()
    const timer = setInterval(fetchIndices, 30000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    api.getHoldingsConverted(currency).then(setHoldingsLocal).catch(()=>{})
  }, [currency])

  // KPI bar from /api/penetration/kpi (real numbers; replaces hardcoded fallback)
  useEffect(() => {
    if (!bizDate) return
    api.getKpi(bizDate).then(d => setKpi(d?.values || null)).catch(() => setKpi(null))
  }, [bizDate])

  // Holdings data for table — always prefer converted API.
  // Must be defined before any useEffect that references it (React hook order).
  const displayHoldings = useMemo(() => {
    const _raw = holdingsLocal.length > 0 ? holdingsLocal : allHoldings
    const _withCurrency = _raw.map(h => ({
      ...h, amount_local: h.amount_local ?? h.amount_cny ?? h.amount,
      currency: h.currency || 'CNY',
    }))
    const CODE_TYPE_MAP = [
      ['.SH', 'a_share_etf'], ['.SZ', 'a_share_etf'],
      ['.HK', 'hk_equity'],
      ['.US', 'us_stock'], ['.OQ', 'us_stock'], ['.NYSE', 'us_stock'], ['.NASDAQ', 'us_stock'],
    ]
    const _typed = _withCurrency.map(h => {
      if (h.asset_type) return h
      for (const [suffix, type] of CODE_TYPE_MAP) {
        if (h.security_code?.endsWith(suffix)) return { ...h, asset_type: type }
      }
      return h
    })
    // 同代码合并（仅显示/统计层，不改底层 holdings 表）
    // quantity/amount/amount_local/amount_original 累加；price = Σ(amount_original)/Σ(quantity) 加权平均
    const grouped = {}
    for (const h of _typed) {
      const key = h.security_code
      if (!grouped[key]) {
        grouped[key] = { ...h, _batch_count: 1 }
      } else {
        const g = grouped[key]
        const newQty = (g.quantity || 0) + (h.quantity || 0)
        const newAmtOrig = (g.amount_original || 0) + (h.amount_original || 0)
        g.quantity = newQty
        g.amount = (g.amount || 0) + (h.amount || 0)
        g.amount_local = (g.amount_local || 0) + (h.amount_local || 0)
        g.amount_original = newAmtOrig
        g.price = (newQty > 0 && newAmtOrig > 0) ? newAmtOrig / newQty : g.price
        g._batch_count += 1
      }
    }
    return Object.values(grouped)
  }, [holdingsLocal, allHoldings])

  // 资产走势（90/180/360 天可切换，曲线=累计收益率%）
  // 抽成 fetchTrend 供 trend-healed 事件复用，避免代码重复
  const fetchTrend = useCallback((days, target) => {
    return api.getTrend(days, target).then(d => {
      const series = d?.series || []
      setTrendData(series)
      if (series.length >= 1) {
        const t0 = series[0].value
        const last = series[series.length - 1].value
        setTrendTotal(last)
        if (t0 > 0) {
          // 每日累计收益率 %（t0 = 0%）
          const ret = (last - t0) / t0 * 100
          setTrendReturn({
            pct: ret,
            abs: last - t0,
            t0,
            returnPctSeries: series.map(p => ((p.value - t0) / t0 * 100)),
          })
        } else {
          setTrendReturn({ pct: 0, abs: 0, t0: 0, returnPctSeries: series.map(() => 0) })
        }
      } else {
        setTrendTotal(null)
        setTrendReturn(null)
      }
    }).catch(() => { setTrendData([]); setTrendTotal(null); setTrendReturn(null) })
  }, [])

  useEffect(() => {
    fetchTrend(trendDays, currency)
  }, [fetchTrend, currency, trendDays])

  // 监听右上角刷新触发的 trend 自愈完成事件，重新拉取走势图
  useEffect(() => {
    const handler = () => fetchTrend(trendDays, currency)
    window.addEventListener('trend-healed', handler)
    return () => window.removeEventListener('trend-healed', handler)
  }, [fetchTrend, currency, trendDays])

  useEffect(() => {
    Promise.all([
      api.getHoldingsSummary().then(setSummary),
      rawApi.get('/holdings').then(r => setAllHoldings(r.data||[])).catch(()=>{}),
      api.getPenetrationTable().then(setPenTable).catch(()=>{}),
      api.getValuation().then(setPe).catch(()=>{}),
      api.getGrowthAnalysis().then(setGrowth).catch(()=>{}),
      api.getDataVersion().then(d => setBizDate(d?.current_business_date)).catch(()=>{}),
    ]).then(() => {
      setTimeout(() => {
        if (pieRef.current) {
          // 复用已有 instance，避免重复 init 警告
          let c = echarts.getInstanceByDom(pieRef.current)
          if (!c) c = echarts.init(pieRef.current)
          // 用 displayHoldings 按 asset_type 聚合，标签与「类型」过滤保持一致（CAT_SHORT）
          const buckets = {}
          displayHoldings.forEach(h => {
            const lbl = CAT_SHORT[h.asset_type] || h.asset_type || '其他'
            buckets[lbl] = (buckets[lbl] || 0) + (h.amount_local || 0)
          })
          const data = Object.entries(buckets).filter(([, v]) => v > 0)
            .map(([k, v]) => ({ name: k, value: Math.round(v) }))
            .sort((a, b) => b.value - a.value)
          c.setOption({
            tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
            series: [{
              type: 'pie', radius: ['45%', '68%'], data,
              label: { color: '#5a6a8a', fontSize: 11 },
              itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
            }],
          })
        }
        if (radarRef.current) {
          // 雷达图替换为主题（type2）构成环形图
          let c = echarts.getInstanceByDom(radarRef.current)
          if (!c) c = echarts.init(radarRef.current)
          const buckets = {}
          displayHoldings.forEach(h => {
            const lbl = h.type2 ? (TYPE2_LABELS[h.type2] || h.type2) : '其他'
            buckets[lbl] = (buckets[lbl] || 0) + (h.amount_local || 0)
          })
          const data = Object.entries(buckets).filter(([, v]) => v > 0)
            .map(([k, v]) => ({ name: k, value: Math.round(v) }))
            .sort((a, b) => b.value - a.value)
          c.setOption({
            tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
            series: [{
              type: 'pie', radius: ['45%', '68%'], data,
              label: { color: '#5a6a8a', fontSize: 11 },
              itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
            }],
          })
        }
        // trend chart 抽到独立 useEffect（避免被其他 API 阻塞）
      }, 100)
    })
  }, [currency, trendData, trendView])

  // trend chart 独立渲染：只依赖 trendData / trendReturn / trendView / currency，立即生效
  useEffect(() => {
    if (!trendRef.current || trendData.length === 0 || !trendReturn) return
    const pctSeries = trendReturn.returnPctSeries || []
    const valueSeries = trendData.map(p => p.value)
    const lastPct = pctSeries.length ? pctSeries[pctSeries.length - 1] : 0
    const lineColor = trendView === 'return'
      ? (lastPct >= 0 ? '#4caf7c' : '#e45a5a')
      : '#4a7cf7'
    const chartData = trendView === 'return'
      ? pctSeries.map(v => Number(v.toFixed(3)))
      : valueSeries.map(v => Math.round(v))
    const yFormatter = trendView === 'return'
      ? v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%'
      : v => (v / 10000).toFixed(0) + '万'
    const valuePrefix = getCurrencySymbol(currency)
    // 复用已有 instance（避免每次 init）
    let c = echarts.getInstanceByDom(trendRef.current)
    if (!c) c = echarts.init(trendRef.current, null, { renderer: 'canvas' })
    c.setOption({
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const p = params[0]
          const idx = p.dataIndex
          const abs = trendData[idx].value
          const pct = pctSeries[idx]
          const sign = pct >= 0 ? '+' : ''
          return `${p.axisValue}<br/>` +
                 `<span style="color:#4caf7c">${sign}${pct.toFixed(2)}%</span><br/>` +
                 `<span style="color:#888">${valuePrefix}${Math.round(abs).toLocaleString('en-US')}</span>`
        },
      },
      grid: { left: 60, right: 16, top: 20, bottom: 30 },
      xAxis: {
        type: 'category',
        data: trendData.map(p => p.date),
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        axisLabel: { color: '#5a6a8a', fontSize: 10, fontFamily: '"GeistMono", monospace' },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#5a6a8a', fontSize: 10, fontFamily: '"GeistMono", monospace', formatter: yFormatter },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      },
      series: [{
        type: 'line',
        data: chartData,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: lineColor, width: 2 },
        areaStyle: { color: lineColor + '22' },
        markLine: trendView === 'return' ? {
          silent: true,
          symbol: 'none',
          lineStyle: { color: 'rgba(255,255,255,0.2)', type: 'dashed' },
          data: [{ yAxis: 0, label: { show: false } }],
        } : undefined,
      }],
    })
    // 容器尺寸变化后主动 resize，避免初始化时宽度被压缩导致只渲染左半
    requestAnimationFrame(() => c.resize())
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(trendRef.current)
    return () => ro.disconnect()
  }, [trendData, trendReturn, trendView, currency])

  const topHoldings = penTable.slice(0, 10)

  // 前 10 大底层持仓（穿透 + 未穿透，仅股票，按前收盘口径）—— 覆盖默认 penTable.slice(0,10)
  // 数据源：/api/penetration/top10-holdings，与 OverviewPanel chip 区口径一致
  const [top10Api, setTop10Api] = useState(null)  // { items: [...], prev_close_date, candidates_total }
  useEffect(() => {
    if (!bizDate) return
    api.getTop10Holdings(bizDate, 10)
      .then(d => setTop10Api(d || null))
      .catch(() => setTop10Api(null))
  }, [bizDate])

  // 类型1：按显示短标签去重（同名 label 合并，如 A股基 = a_share_equity ∪ a_share_etf）
  const type1Labels = [...new Set(displayHoldings.map(h => CAT_SHORT[h.asset_type] || h.asset_type).filter(Boolean))]

  // 主题：先把 raw type2 翻成 displayLabel，再去重（"emerging" + "新兴产业" 都显示为 "新兴产业"）
  const type2Display = (raw) => TYPE2_LABELS[raw] || raw
  const hasEmptyType2 = displayHoldings.some(h => !h.type2)
  const type2Labels = [
    ...[...new Set(displayHoldings.map(h => h.type2).filter(Boolean).map(type2Display))],
    ...(hasEmptyType2 ? ['其他'] : []),
  ]

  // Filter by type1 + type2 (both must match if active)
  const filteredHoldings = displayHoldings.filter(h => {
    if (typeFilter !== 'all') {
      const lbl = CAT_SHORT[h.asset_type] || h.asset_type
      if (lbl !== typeFilter) return false
    }
    if (type2Filter !== 'all') {
      if (type2Filter === '其他') {
        if (h.type2) return false
      } else {
        if (type2Display(h.type2) !== type2Filter) return false
      }
    }
    return true
  })

  const sortedHoldings = [...filteredHoldings].sort((a,b) => {
    const aV = a[sortKey]||0, bV = b[sortKey]||0
    return sortDir === 'desc' ? bV - aV : aV - bV
  })
  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }
  // 总资产 = 持仓市值 + 现金。现金来自 HoldingDailySnapshot 最新日 CASH 行（summary.cash_cny，CNY 口径）。
  // 非 CNY 视图下不叠加（cash_cny 未做汇率换算），保持口径一致避免误差。
  const cashAmount = (currency === 'CNY' && summary?.cash_cny) ? summary.cash_cny : 0
  const totalAmtLocal = displayHoldings.reduce((s,h) => s + (h.amount_local || h.amount || 0), 0) + cashAmount
  const filteredTotal = filteredHoldings.reduce((s,h) => s + (h.amount_local || h.amount || 0), 0)

  return (
    <div>
      {/* 3×2 KPI Grid */}
      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-label">总资产</div>
          <div className="kpi-value">{fmtAmount(totalAmtLocal, getCurrencySymbol(currency))}</div>
          <div className="kpi-sub">{currency}</div>
        </div>
        <div className="kpi-card"><div className="kpi-label">穿透股票</div><div className="kpi-value">{kpi ? kpi.drilled_stock_count : (penTable.length || '—')}</div><div className="kpi-sub">{summary?.fund_count||0}基金</div></div>
        <div className="kpi-card">
          <div className="kpi-label">基金下钻 PE</div>
          <div className="kpi-value">{kpi?.portfolio_pe_weighted?.toFixed(1) || '—'}</div>
          <div className="kpi-sub">300: {(kpi?.csi300_pe ?? pe.csi300_pe)?.toFixed(1) || '—'}</div>
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
              ? `新兴 ${(kpi.tech_weight_breakdown.emerging_cny/10000).toFixed(0)}w + 美科 ${(kpi.tech_weight_breakdown.us_tech_cny/10000).toFixed(0)}w`
              : '未加载'}
          </div>
        </div>
      </div>

      {/* 市场指数涨跌幅 */}
      <div className="kpi-grid" style={{ gridTemplateColumns: 'repeat(6, 1fr)', marginTop: 4 }}>
        {marketIndices.map(idx => (
          <div className="kpi-card" key={idx.code}>
            <div className="kpi-label">{idx.name}</div>
            <div className="kpi-value" style={{
              color: idx.change_pct == null ? undefined
                     : (idx.change_pct > 0 ? 'var(--up)'
                     : idx.change_pct < 0 ? 'var(--down)' : undefined),
              fontWeight: 600,
              fontSize: 18,
            }}>
              {idx.change_pct != null
                ? (idx.change_pct > 0 ? '+' : '') + idx.change_pct.toFixed(2) + '%'
                : '—'}
            </div>
            <div className="kpi-sub" style={{ fontSize: 10 }}>当日涨跌幅</div>
          </div>
        ))}
      </div>

      {/* 本位币切换（总资产下方） */}
      <div className="raised" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace', letterSpacing: 0.5 }}>本位币</span>
        {['CNY', 'USD', 'CAD'].map(c => (
          <button
            key={c}
            onClick={() => setCurrency(c)}
            className={currency === c ? 'cur-btn on' : 'cur-btn'}
          >
            {c}
          </button>
        ))}
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto', fontFamily: '"GeistMono", monospace' }}>
          折算汇率见「设置 · 汇率」
        </span>
      </div>

      {/* 资产走势 (90 / 180 / 360 天 + 资产净值/收益率 切换) */}
      <div className="raised" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '8px 12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div className="section-title" style={{ marginBottom: 0 }}>资产走势</div>
            {/* 90 / 180 / 360 标签式切换 */}
            <div style={{ display: 'flex', gap: 2 }}>
              {[90, 180, 360].map(d => (
                <button key={d}
                  onClick={() => setTrendDays(d)}
                  className={trendDays === d ? 'cur-btn on' : 'cur-btn'}
                  style={{ fontSize: 10, padding: '2px 8px' }}>
                  {d}天
                </button>
              ))}
            </div>
            {/* 资产净值 / 收益率 切换 */}
            <div style={{ display: 'flex', gap: 2, marginLeft: 4 }}>
              <button
                onClick={() => setTrendView('value')}
                className={trendView === 'value' ? 'cur-btn on' : 'cur-btn'}
                style={{ fontSize: 10, padding: '2px 8px' }}
                title="Y 轴显示资产净值（绝对值）">
                资产净值
              </button>
              <button
                onClick={() => setTrendView('return')}
                className={trendView === 'return' ? 'cur-btn on' : 'cur-btn'}
                style={{ fontSize: 10, padding: '2px 8px' }}
                title="Y 轴显示累计收益率%（t0=0%）">
                收益率
              </button>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontFamily: '"GeistMono", monospace' }}>
            {/* 累计收益率标签（始终显示） */}
            <span style={{
              fontSize: 13, fontWeight: 700,
              color: trendReturn
                ? (trendReturn.pct >= 0 ? 'var(--chart-up)' : 'var(--chart-down)')
                : 'var(--text-muted)',
              minWidth: 64, textAlign: 'right',
            }} title="累计收益率（自 t0 起）">
              {trendReturn
                ? `${trendReturn.pct >= 0 ? '+' : ''}${trendReturn.pct.toFixed(2)}%`
                : '—'}
            </span>
            <span style={{ width: 1, height: 14, background: 'var(--border)' }} />
            {/* 当前资产价值标签（始终显示） */}
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }} title="当前资产价值">
              {trendTotal != null
                ? getCurrencySymbol(currency) + Math.round(trendTotal).toLocaleString('en-US')
                : '—'}
            </span>
          </div>
        </div>
        <div ref={trendRef} className="chart-box" style={{ width: '100%', height: 360, minWidth: 0 }} />
      </div>

      {/* Charts */}
      <div className="chart-grid">
        <div className="raised"><div className="section-title">资产分布</div><div ref={pieRef} className="chart-box" /></div>
        <div className="raised"><div className="section-title">主题构成</div><div ref={radarRef} className="chart-box" /></div>
      </div>

      {/* Top Holdings Chips */}
      <div className="raised">
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',marginBottom:6}}>
          <div className="section-title" style={{marginBottom:0}}>前10大底层持仓</div>
          <span style={{fontSize:10,color:'var(--text-muted)',fontFamily:'"GeistMono",monospace'}}>
            {top10Api?.prev_close_date
              ? `按 ${top10Api.prev_close_date} 前收 · ${top10Api.candidates_total} 候选`
              : '加载中…'}
          </span>
        </div>
        <div style={{display:'flex',flexWrap:'wrap',gap:6}}>
          {(() => {
            // 优先用 top10Api（穿透 + 未穿透，股票，前收口径）；
            // 加载失败 / 业务日期未就绪时回退到旧 penTable.slice(0,10)。
            const rows = top10Api?.items?.length
              ? top10Api.items.map(r => ({
                  stock_code: r.stock_code,
                  stock_name: r.stock_name,
                  penetration_weight: r.weight_pct,   // 字段名沿用 chip UI 兼容
                  ttm_pe: r.pe_ttm,
                }))
              : topHoldings.filter(r => r.stock_name && !r.stock_code.includes('.OF'));
            return rows.map(r => (
              <div key={r.stock_code} className="chip">
                <span>{r.stock_name}</span>
                <span className="pct">{r.penetration_weight.toFixed(1)}%</span>
                <span className="pe">PE {r.ttm_pe?.toFixed(1)||'-'}</span>
              </div>
            ));
          })()}
        </div>
      </div>

      {/* Holdings Table */}
      <div className="raised" style={{padding:0,overflow:'hidden'}}>
        <div style={{padding:'12px 16px 8px',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <div className="section-title" style={{marginBottom:0}}>全部持仓 · {filteredHoldings.length}项</div>
          <div style={{display:'flex',gap:8,alignItems:'center'}}>
            <span style={{fontSize:12,color:'var(--text-muted)'}}>合计 {getCurrencySymbol(currency)}{Math.round(filteredTotal).toLocaleString('en-US')}</span>
          </div>
        </div>
        {/* Type1 filter (asset_type) — 同 label 合并（不同 label 自然分开） */}
        <div style={{padding:'0 16px 4px',display:'flex',gap:4,flexWrap:'wrap',alignItems:'center'}}>
          <span style={{fontSize:11,color:'var(--text-muted)',marginRight:4,fontFamily:'"GeistMono",monospace',letterSpacing:0.5}}>类型</span>
          <button onClick={()=>setTypeFilter('all')} className={typeFilter==='all' ? 'cur-btn on' : 'cur-btn'} style={{fontSize:10}}>全部</button>
          {type1Labels.map(lbl => (
            <button key={lbl} onClick={()=>setTypeFilter(lbl)} className={typeFilter===lbl ? 'cur-btn on' : 'cur-btn'} style={{fontSize:10}}>
              {lbl}
            </button>
          ))}
        </div>
        {/* Type2 filter (theme: 红利/新兴产业/黄金/其他) */}
        {type2Labels.length > 0 && (
          <div style={{padding:'0 16px 8px',display:'flex',gap:4,flexWrap:'wrap',alignItems:'center'}}>
            <span style={{fontSize:11,color:'var(--text-muted)',marginRight:4,fontFamily:'"GeistMono",monospace',letterSpacing:0.5}}>主题</span>
            <button onClick={()=>setType2Filter('all')} className={type2Filter==='all' ? 'cur-btn on' : 'cur-btn'} style={{fontSize:10}}>全部</button>
            {type2Labels.map(lbl => (
              <button key={lbl} onClick={()=>setType2Filter(lbl)} className={type2Filter===lbl ? 'cur-btn on' : 'cur-btn'} style={{fontSize:10}}>
                {lbl}
              </button>
            ))}
          </div>
        )}
        <div className="table-wrap" style={{maxHeight:400,overflowY:'auto'}}>
          <table className="data-table">
            <colgroup>
              <col style={{width:'86px'}}/>
              <col style={{width:'300px'}}/>
              <col style={{width:'46px'}}/>
              <col style={{width:'62px'}}/>
              <col style={{width:'78px'}}/>
              <col style={{width:'88px'}}/>
              <col style={{width:'100px'}}/>
              <col style={{width:'108px'}}/>
            </colgroup>
            <thead>
              <tr>
                <th style={{cursor:'pointer'}} onClick={()=>toggleSort('security_code')}>代码</th>
                <th style={{cursor:'pointer'}} onClick={()=>toggleSort('security_name')}>名称</th>
                <th style={{textAlign:'center'}}>类型</th>
                <th style={{textAlign:'right',cursor:'pointer'}} onClick={()=>toggleSort('amount_local')}>占比</th>
                <th style={{textAlign:'right',cursor:'pointer'}} onClick={()=>toggleSort('quantity')}>数量</th>
                <th style={{textAlign:'right',cursor:'pointer'}} onClick={()=>toggleSort('price')}>单价·原</th>
                <th style={{textAlign:'right',cursor:'pointer'}} onClick={()=>toggleSort('amount_original')}>金额·原</th>
                <th style={{textAlign:'right',cursor:'pointer'}} onClick={()=>toggleSort('amount')}>金额·本</th>
              </tr>
            </thead>
            <tbody>
              {sortedHoldings.map(h => {
                const ratio = totalAmtLocal > 0 ? (h.amount_local ?? (h.amount || 0)) / totalAmtLocal : 0
                const amountOrig = h.amount_original ?? (h.price && h.quantity ? Math.round(h.quantity * h.price * 100) / 100 : null)
                const origSymbol = getCurrencySymbol(h.currency || 'CNY')
                return (
                <tr key={h.id || `${h.security_code}#${h.quantity}#${h.amount}`}>
                  <td title={h.security_code} style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{h.security_code}</td>
                  <td title={h.security_name} style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                    {drillableCodes.has(h.security_code) && (
                      <span style={{
                        display:'inline-block', width:'16px', height:'16px', lineHeight:'16px',
                        textAlign:'center', fontSize:9, fontWeight:700, fontFamily:'"GeistMono",monospace',
                        color:'#ffd700', background:'rgba(255,215,0,0.12)',
                        border:'1px solid rgba(255,215,0,0.4)', borderRadius:2,
                        marginRight:5, verticalAlign:'middle', flexShrink:0,
                      }} title="可下钻基金">钻</span>
                    )}
                    {h.security_name||'-'}
                  </td>
                  <td style={{textAlign:'center',color:'var(--text-secondary)',fontSize:11}}>{CAT_SHORT[h.asset_type]||h.asset_type||''}</td>
                  <td style={{textAlign:'right',fontFamily:'"GeistMono",monospace',color:'var(--text-secondary)'}}>{fmtPct(ratio)}</td>
                  <td style={{textAlign:'right',fontFamily:'"GeistMono",monospace'}}>{fmtQty(h.quantity)}</td>
                  <td style={{textAlign:'right',fontFamily:'"GeistMono",monospace',color:'var(--text-secondary)'}}>{h.price ? h.price.toFixed(h.price_precision ?? 2) : '-'}</td>
                  <td style={{textAlign:'right',fontFamily:'"GeistMono",monospace',color:'var(--text-secondary)'}}>{amountOrig != null ? origSymbol + Math.round(amountOrig).toLocaleString('en-US') : '-'}</td>
                  <td style={{textAlign:'right',fontFamily:'"GeistMono",monospace',fontWeight:600}}>{fmtAmount(h.amount_local ?? h.amount, getCurrencySymbol(currency))}</td>
                </tr>
              )})}
              {/* Summary row */}
              <tr style={{borderTop:'1px solid var(--border-strong)',fontWeight:600}}>
                <td colSpan={3} style={{color:'var(--text-muted)',fontSize:11}}>合计</td>
                <td style={{textAlign:'right',fontFamily:'"GeistMono",monospace',color:'var(--text-secondary)'}}>{totalAmtLocal > 0 ? fmtPct(filteredTotal / totalAmtLocal) : '-'}</td>
                <td></td>
                <td></td>
                <td></td>
                <td style={{textAlign:'right',fontFamily:'"GeistMono",monospace',fontWeight:700}}>{fmtAmount(filteredTotal, getCurrencySymbol(currency))}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
