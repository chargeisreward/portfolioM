import React, { useState } from 'react'

export default function TradingPanel() {
  const [mode, setMode] = useState('form')
  const [rows, setRows] = useState([{ code:'', name:'', direction:'buy', shares:'', price:'', date:new Date().toISOString().slice(0,10) }])
  const [fxDir, setFxDir] = useState('CNYtoUSD')
  const [fxAmt, setFxAmt] = useState('')
  const [fxRate, setFxRate] = useState(7.25)
  const [fxDate, setFxDate] = useState(new Date().toISOString().slice(0,10))
  const [fxHistory, setFxHistory] = useState([])

  const addRow = () => setRows([...rows, { code:'',name:'',direction:'buy',shares:'',price:'',date:new Date().toISOString().slice(0,10) }])
  const updRow = (i, k, v) => { const n=[...rows]; n[i][k]=v; setRows(n) }
  const delRow = (i) => { if (rows.length>1) setRows(rows.filter((_,j)=>j!==i)) }

  return (
    <div>
      <div className="raised">
        <div className="section-title">交易模式</div>
        <div style={{display:'flex',gap:12,marginBottom:12}}>
          <label style={{display:'flex',alignItems:'center',gap:6,color:'var(--text-primary)',fontSize:14}}>
            <input type="radio" checked={mode==='form'} onChange={()=>setMode('form')} /> 直接填写
          </label>
          <label style={{display:'flex',alignItems:'center',gap:6,color:'var(--text-primary)',fontSize:14}}>
            <input type="radio" checked={mode==='upload'} onChange={()=>setMode('upload')} /> 上传文件
          </label>
        </div>
        {mode==='upload' && (
          <div style={{border:'2px dashed var(--glass-border)',borderRadius:'var(--radius-md)',padding:30,textAlign:'center',color:'var(--text-secondary)'}}>
            <p>拖拽 .xlsx/.csv 文件到此处</p>
            <p style={{fontSize:12,marginTop:8}}>或 <label style={{color:'var(--accent-primary)',cursor:'pointer',textDecoration:'underline'}}>选择文件<input type="file" hidden accept=".xlsx,.csv" onChange={e=>e.target.files[0]&&alert('已选择: '+e.target.files[0].name)} /></label></p>
          </div>
        )}
      </div>

      <div className="raised">
        <div className="section-title">交易表单</div>
        {rows.map((r,i)=>(
          <div key={i} style={{display:'flex',gap:8,marginBottom:8,flexWrap:'wrap'}}>
            <input className="ig" style={{width:100}} placeholder="代码" value={r.code} onChange={e=>updRow(i,'code',e.target.value)} />
            <input className="ig" style={{width:130}} placeholder="名称" value={r.name} onChange={e=>updRow(i,'name',e.target.value)} />
            <select className="ig" style={{width:80}} value={r.direction} onChange={e=>updRow(i,'direction',e.target.value)}>
              <option value="buy">买入</option><option value="sell">卖出</option>
            </select>
            <input className="ig" style={{width:80}} placeholder="数量" type="number" value={r.shares} onChange={e=>updRow(i,'shares',e.target.value)} />
            <input className="ig" style={{width:90}} placeholder="价格" type="number" step="0.001" value={r.price} onChange={e=>updRow(i,'price',e.target.value)} />
            <input className="ig" style={{width:130}} type="date" value={r.date} onChange={e=>updRow(i,'date',e.target.value)} />
            <button className="btn-glass" style={{padding:'6px 10px',fontSize:12}} onClick={()=>delRow(i)}>✕</button>
          </div>
        ))}
        <div style={{display:'flex',gap:8}}>
          <button className="btn-glass" onClick={addRow}>+ 添加一行</button>
          <button className="btn-glass" style={{background:'var(--accent)',border:'none',color:'#fff'}} onClick={()=>alert(`提交 ${rows.length} 笔交易`)}>提交交易</button>
        </div>
      </div>

      <div className="raised">
        <div className="section-title">外汇转账</div>
        <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
          <select className="ig" style={{width:130}} value={fxDir} onChange={e=>setFxDir(e.target.value)}>
            <option value="CNYtoUSD">CNY → USD</option><option value="USDtoCNY">USD → CNY</option>
          </select>
          <input className="ig" style={{width:100}} placeholder="金额" type="number" value={fxAmt} onChange={e=>setFxAmt(e.target.value)} />
          <input className="ig" style={{width:90}} placeholder="汇率" type="number" step="0.01" value={fxRate} onChange={e=>setFxRate(e.target.value)} />
          <input className="ig" style={{width:130}} type="date" value={fxDate} onChange={e=>setFxDate(e.target.value)} />
          <button className="btn-glass" style={{background:'var(--accent)',border:'none',color:'#fff'}} onClick={()=>setFxHistory([{date:fxDate,dir:fxDir==='CNYtoUSD'?'CNY→USD':'USD→CNY',amt:fxAmt,rate:fxRate,status:'pending'},...fxHistory])}>记录转账</button>
        </div>
        {fxHistory.length>0 && (
          <table className="data-table" style={{marginTop:12}}>
            <thead><tr><th>日期</th><th>方向</th><th>金额</th><th>汇率</th><th>状态</th></tr></thead>
            <tbody>{fxHistory.map((f,i)=><tr key={i}><td>{f.date}</td><td>{f.dir}</td><td>{f.amt}</td><td>{f.rate}</td><td style={{color:f.status==='confirmed'?'var(--chart-up)':'var(--text-muted)'}}>{f.status==='confirmed'?'✅ 已入账':'⚠️ 待确认'}</td></tr>)}</tbody>
          </table>
        )}
      </div>
    </div>
  )
}
