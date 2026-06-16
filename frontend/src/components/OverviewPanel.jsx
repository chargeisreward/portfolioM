import React, { useState, useEffect, useRef, useMemo } from 'react'
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
  const [trendReturn, setTrendReturn] = useState(null)  // {pct, abs} over the window
  const [sortKey, setSortKey] = useState('amount')
  const [sortDir, setSortDir] = useState('desc')
  const [currency, setCurrency] = useState('CNY')
  const [holdingsLocal, setHoldingsLocal] = useState([])
  const [typeFilter, setTypeFilter] = useState('all')
  const [type2Filter, setType2Filter] = useState('all')

  useEffect(() => {
    api.getHoldingsConverted(currency).then(setHoldingsLocal).catch(()=>{})
  }, [currency])

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
    return _withCurrency.map(h => {
      if (h.asset_type) return h
      for (const [suffix, type] of CODE_TYPE_MAP) {
        if (h.security_code?.endsWith(suffix)) return { ...h, asset_type: type }
      }
      return h
    })
  }, [holdingsLocal, allHoldings])

  // 资产走势（90/180/360 天可切换）
  useEffect(() => {
    api.getTrend(trendDays, currency).then(d => {
      const series = d?.series || []
      setTrendData(series)
      if (series.length >= 2) {
        const first = series[0].value
        const last = series[series.length - 1].value
        setTrendTotal(last)
        setTrendReturn({
          pct: first > 0 ? (last - first) / first * 100 : null,
          abs: last - first,
        })
      } else if (series.length === 1) {
        setTrendTotal(series[0].value)
        setTrendReturn({ pct: null, abs: 0 })
      } else {
        setTrendTotal(null)
        setTrendReturn(null)
      }
    }).catch(() => { setTrendData([]); setTrendTotal(null); setTrendReturn(null) })
  }, [currency, trendDays])

  useEffect(() => {
    Promise.all([
      api.getHoldingsSummary().then(setSummary),
      rawApi.get('/holdings').then(r => setAllHoldings(r.data||[])).catch(()=>{}),
      api.getPenetrationTable().then(setPenTable).catch(()=>{}),
      api.getValuation().then(setPe).catch(()=>{}),
      api.getGrowthAnalysis().then(setGrowth).catch(()=>{}),
    ]).then(() => {
      setTimeout(() => {
        if (pieRef.current) {
          const c = echarts.init(pieRef.current)
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
          const c = echarts.init(radarRef.current)
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
        if (trendRef.current && trendData.length > 0) {
          const c = echarts.init(trendRef.current)
          c.setOption({
            tooltip: { trigger: 'axis' },
            grid: { left: 60, right: 16, top: 20, bottom: 30 },
            xAxis: {
              type: 'category',
              data: trendData.map(p => p.date),
              axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
              axisLabel: { color: '#5a6a8a', fontSize: 10, fontFamily: '"GeistMono", monospace' },
            },
            yAxis: {
              type: 'value',
              axisLabel: { color: '#5a6a8a', fontSize: 10, fontFamily: '"GeistMono", monospace', formatter: v => (v/10000).toFixed(0) + '万' },
              splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
            },
            series: [{
              type: 'line',
              data: trendData.map(p => Math.round(p.value)),
              smooth: true,
              showSymbol: false,
              lineStyle: { color: '#4a7cf7', width: 2 },
              areaStyle: { color: 'rgba(74,124,247,0.1)' },
            }],
          })
        }
      }, 100)
    })
  }, [currency, trendData])

  const topHoldings = penTable.slice(0, 10)

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
  const totalAmtLocal = displayHoldings.reduce((s,h) => s + (h.amount_local || h.amount || 0), 0)
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
        <div className="kpi-card"><div className="kpi-label">穿透股票</div><div className="kpi-value">{penTable.length}</div><div className="kpi-sub">{summary?.fund_count||0}基金</div></div>
        <div className="kpi-card"><div className="kpi-label">组合PE</div><div className="kpi-value">{pe.portfolio_weighted_pe?.toFixed(1)||'-'}</div><div className="kpi-sub">300: {pe.csi300_pe?.toFixed(1)||'-'}</div></div>
        <div className="kpi-card"><div className="kpi-label">高增长%</div><div className="kpi-value kpi-up">{growth.portfolio?.high?.toFixed(1)||'-'}%</div><div className="kpi-sub">300: {growth.csi300?.high?.toFixed(1)||'-'}%</div></div>
        <div className="kpi-card"><div className="kpi-label">Forecast PE</div><div className="kpi-value">{pe.portfolio_forecast_pe_1y?.toFixed(1)||'-'}</div><div className="kpi-sub">1年后预期</div></div>
        <div className="kpi-card"><div className="kpi-label">中游占比</div><div className="kpi-value">36%</div><div className="kpi-sub">半导体+设备</div></div>
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

      {/* 资产走势 (90 / 180 / 360 天可切换) */}
      <div className="raised" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '8px 12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
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
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontFamily: '"GeistMono", monospace' }}>
            {/* 收益率标签 */}
            {trendReturn && trendReturn.pct != null && (
              <span style={{
                fontSize: 12, fontWeight: 600,
                color: trendReturn.pct >= 0 ? 'var(--chart-up)' : 'var(--chart-down)',
              }}>
                {trendReturn.pct >= 0 ? '+' : ''}{trendReturn.pct.toFixed(2)}%
              </span>
            )}
            {/* 当前资产价值标签 */}
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              {trendTotal != null
                ? getCurrencySymbol(currency) + Math.round(trendTotal).toLocaleString('en-US')
                : '加载中…'}
            </span>
          </div>
        </div>
        <div ref={trendRef} className="chart-box" style={{ width: '100%', height: 240 }} />
      </div>

      {/* Charts */}
      <div className="chart-grid">
        <div className="raised"><div className="section-title">资产分布</div><div ref={pieRef} className="chart-box" /></div>
        <div className="raised"><div className="section-title">主题构成</div><div ref={radarRef} className="chart-box" /></div>
      </div>

      {/* Top Holdings Chips */}
      <div className="raised">
        <div className="section-title">前10大底层持仓</div>
        <div style={{display:'flex',flexWrap:'wrap',gap:6}}>
          {topHoldings.filter(r => r.stock_name && !r.stock_code.includes('.OF')).map(r => (
            <div key={r.stock_code} className="chip">
              <span>{r.stock_name}</span>
              <span className="pct">{r.penetration_weight.toFixed(1)}%</span>
              <span className="pe">PE {r.ttm_pe?.toFixed(1)||'-'}</span>
            </div>
          ))}
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
                  <td title={h.security_name} style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{h.security_name||'-'}</td>
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
