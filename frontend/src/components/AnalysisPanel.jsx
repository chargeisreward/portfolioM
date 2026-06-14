import React, { useState, useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import * as api from '../api'

const DIMS = [
  { id:'chain', label:'产业链' }, { id:'growth', label:'增长分层' }, { id:'valuation', label:'估值' },
  { id:'competition', label:'竞争格局' }, { id:'risk', label:'风险因子' }, { id:'correlation', label:'相关性' }, { id:'outlook', label:'景气度' },
]

export default function AnalysisPanel() {
  const [active, setActive] = useState('chain')
  const chartRef = useRef(null)
  const [drill, setDrill] = useState([])
  const [penTable, setPenTable] = useState([])
  const [penSort, setPenSort] = useState({ key: 'penetration_weight', dir: 'desc' })

  useEffect(() => {
    api.getPenetrationTable().then(setPenTable).catch(() => {})
  }, [])

  const sortedPen = [...penTable].sort((a, b) => {
    const aV = a[penSort.key] || 0; const bV = b[penSort.key] || 0
    return penSort.dir === 'desc' ? bV - aV : aV - bV
  })

  const togglePenSort = (key) => {
    if (penSort.key === key) setPenSort(p => ({ key, dir: p.dir === 'desc' ? 'asc' : 'desc' }))
    else setPenSort({ key, dir: 'desc' })
  }

  const CHAIN = { upstream:'上游', midstream:'中游', downstream:'下游', financial:'金融', other:'其他', bond:'债券', gold:'黄金' }
  const GROWTH = { high:'高增长', medium:'中增长', low:'低增长', unknown:'-' }

  useEffect(() => {
    const chart = echarts.init(chartRef.current)
    const load = async () => {
      if (active === 'chain') {
        const data = await api.getIndustryChain()
        const labels = { upstream:'上游', midstream:'中游', downstream:'下游', financial:'金融', bond:'债券', gold:'黄金', other:'其他' }
        const cats = Object.keys(data.portfolio)
        chart.setOption({
          tooltip:{trigger:'axis'}, legend:{data:['组合','沪深300'],textStyle:{color:'#9ca3af'}},
          xAxis:{type:'category',data:cats.map(c=>labels[c]||c),axisLabel:{color:'#9ca3af'}},
          yAxis:{type:'value',name:'权重%',axisLabel:{color:'#9ca3af'},nameTextStyle:{color:'#9ca3af'}},
          series:[
            {name:'组合',type:'bar',data:cats.map(c=>data.portfolio[c]||0),itemStyle:{color:'#4a7cf7'}},
            ...(data.csi300 ? [{name:'沪深300',type:'bar',data:cats.map(c=>data.csi300[c]||0),itemStyle:{color:'rgba(255,255,255,0.5)'}}] : []),
          ],
        })
        chart.off('click'); chart.on('click', p => { setDrill([{stock_code:'-',stock_name:`${labels[cats[p.dataIndex]]||cats[p.dataIndex]} 成分股`,weight:data.portfolio[cats[p.dataIndex]],value:`${(data.portfolio[cats[p.dataIndex]]||0).toFixed(1)}%`}]) })
      } else if (active === 'growth') {
        const data = await api.getGrowthAnalysis()
        chart.setOption({
          tooltip:{trigger:'axis'}, legend:{data:['组合','沪深300'],textStyle:{color:'#9ca3af'}},
          xAxis:{type:'category',data:['高增长','中增长','低增长'],axisLabel:{color:'#9ca3af'}},
          yAxis:{type:'value',name:'权重%',axisLabel:{color:'#9ca3af'},nameTextStyle:{color:'#9ca3af'}},
          series:[
            {name:'组合',type:'bar',data:[data.portfolio.high||0,data.portfolio.medium||0,data.portfolio.low||0],itemStyle:{color:'#4a7cf7'}},
            ...(data.csi300 ? [{name:'沪深300',type:'bar',data:[data.csi300.high||0,data.csi300.medium||0,data.csi300.low||0],itemStyle:{color:'rgba(255,255,255,0.5)'}}] : []),
          ],
        })
        chart.off('click'); chart.on('click', p => setDrill([{stock_code:'-',stock_name:`${p.name} 成分股`,weight:100,value:`${p.value.toFixed(1)}%`}]))
      } else if (active === 'valuation') {
        const data = await api.getValuation()
        chart.setOption({
          tooltip:{trigger:'axis'}, legend:{data:['组合','沪深300'],textStyle:{color:'#9ca3af'}},
          xAxis:{type:'category',data:['TTM PE','Forecast 1Y','Forecast 2Y'],axisLabel:{color:'#9ca3af'}},
          yAxis:{type:'value',name:'PE',axisLabel:{color:'#9ca3af'},nameTextStyle:{color:'#9ca3af'}},
          series:[
            {name:'组合',type:'bar',data:[data.portfolio_weighted_pe,data.portfolio_forecast_pe_1y,data.portfolio_forecast_pe_2y].map(v=>v||0),itemStyle:{color:'#4a7cf7'}},
            {name:'沪深300',type:'bar',data:[data.csi300_pe,null,null].map(v=>v||0),itemStyle:{color:'rgba(255,255,255,0.5)'}},
          ],
        })
        chart.off('click'); chart.on('click', p => setDrill([{stock_code:'-',stock_name:p.name,weight:100,value:`${(p.value||0).toFixed(1)}x`}]))
      } else { chart.clear(); setDrill([]) }
    }
    load()
    const handleResize = () => chart.resize()
    window.addEventListener('resize', handleResize)
    return () => { window.removeEventListener('resize', handleResize); chart.dispose() }
  }, [active])

  return (
    <div>
      {/* Compact Tabs */}
      <div className="raised" style={{padding:'6px 12px',marginBottom:10}}>
        <div style={{display:'flex',gap:2,flexWrap:'wrap'}}>
          {DIMS.map(d => (
            <button key={d.id} onClick={()=>setActive(d.id)}
              style={{
                padding:'5px 12px', border:'none', borderRadius:6,
                background: active===d.id ? 'var(--accent)' : 'transparent',
                color: active===d.id ? '#fff' : 'var(--text-secondary)',
                cursor:'pointer', fontSize:12, fontWeight: active===d.id ? 600 : 400,
                transition:'all 0.15s',
              }}>{d.label}</button>
          ))}
        </div>
      </div>

      {/* Metrics Bar */}
      <div className="kpi-grid" style={{marginBottom:10}}>
        <div className="kpi-card"><div className="kpi-label">组合PE</div><div className="kpi-value">36.5</div><div className="kpi-sub">沪深300 21.2</div></div>
        <div className="kpi-card"><div className="kpi-label">Forecast 1Y</div><div className="kpi-value">30.3</div><div className="kpi-sub">-17% vs TTM</div></div>
        <div className="kpi-card"><div className="kpi-label">高增长</div><div className="kpi-value kpi-up">38.9%</div><div className="kpi-sub">沪深300 52.8%</div></div>
        <div className="kpi-card"><div className="kpi-label">中游占比</div><div className="kpi-value">25.8%</div><div className="kpi-sub">沪深300 21.4%</div></div>
        <div className="kpi-card"><div className="kpi-label">溢价率</div><div className="kpi-value kpi-down">+72%</div><div className="kpi-sub">PE vs 沪深300</div></div>
        <div className="kpi-card"><div className="kpi-label">穿透深度</div><div className="kpi-value">83</div><div className="kpi-sub">只底层股票</div></div>
      </div>

      <div className="chart-grid">
        <div className="raised" style={{minHeight:400}}>
          <div className="section-title">{DIMS.find(d=>d.id===active)?.label} — 组合 vs 沪深300</div>
          <div ref={chartRef} className="chart-box" />
        </div>
        <div className="raised" style={{minHeight:400}}>
          <div className="section-title">下钻详情</div>
          {drill.length > 0 ? (
            <table className="data-table">
              <thead><tr><th>代码</th><th>名称</th><th>权重%</th><th>数值</th></tr></thead>
              <tbody>{drill.map((r,i)=><tr key={i}><td>{r.stock_code}</td><td>{r.stock_name}</td><td>{(r.weight||0).toFixed(2)}</td><td>{r.value}</td></tr>)}</tbody>
            </table>
          ) : <div style={{color:'var(--text-muted)',padding:40,textAlign:'center'}}>点击左侧图表数据点查看明细</div>}
        </div>
      </div>

      {/* ─── Full Penetration Table ─── */}
      <div className="raised">
        <div className="section-title">全部穿透持仓 — {penTable.length} 只底层股票</div>
        <div style={{fontSize:12,color:'var(--text-secondary)',marginBottom:8}}>点击表头排序</div>
        <div className="table-wrap" style={{maxHeight:500,overflowY:'auto'}}>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{cursor:'pointer'}} onClick={()=>togglePenSort('stock_code')}>代码</th>
                <th style={{cursor:'pointer'}} onClick={()=>togglePenSort('stock_name')}>名称</th>
                <th style={{cursor:'pointer'}} onClick={()=>togglePenSort('penetration_weight')}>
                  权重% {penSort.key==='penetration_weight'?(penSort.dir==='desc'?'▼':'▲'):''}
                </th>
                <th style={{cursor:'pointer'}} onClick={()=>togglePenSort('ttm_pe')}>
                  PE {penSort.key==='ttm_pe'?(penSort.dir==='desc'?'▼':'▲'):''}
                </th>
                <th style={{cursor:'pointer'}} onClick={()=>togglePenSort('profit_growth')}>
                  利润增速 {penSort.key==='profit_growth'?(penSort.dir==='desc'?'▼':'▲'):''}
                </th>
                <th>产业链</th><th>增长</th><th>Forecast PE</th>
              </tr>
            </thead>
            <tbody>
              {sortedPen.map(r => (
                <tr key={r.stock_code||r.id}>
                  <td>{r.stock_code}</td>
                  <td>{r.stock_name||'-'}</td>
                  <td>{r.penetration_weight?.toFixed(2)}</td>
                  <td>{r.ttm_pe?.toFixed(1)||'-'}</td>
                  <td style={{color:r.profit_growth>=0?'var(--chart-up)':'var(--chart-down)'}}>
                    {r.profit_growth!=null?r.profit_growth.toFixed(1)+'%':'-'}
                  </td>
                  <td>{CHAIN[r.chain_position]||'-'}</td>
                  <td>{GROWTH[r.growth_tier]||'-'}</td>
                  <td>{r.forecast_pe_1y?.toFixed(1)||'-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
