import React from 'react'
import * as api from '../api'
import TradingCalendarView from './TradingCalendarView'

/**
 * 管理员设置面板 — 数据管理 / 交易日历 / 数据状态
 * 仅管理员可见（TABS visibility: ['admin']）
 */
export default function AdminSettingsPanel() {
  return (
    <div>
      <div className="raised">
        <div className="section-title">📊 数据管理</div>
        <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
          <button className="btn-glass" onClick={()=>api.postImport().then(r=>alert(r.message))}>📥 导入Excel</button>
          <button className="btn-glass" onClick={()=>api.postCrawlAll().then(r=>alert(r.message))}>🕷 重新爬取</button>
          <button className="btn-glass" onClick={()=>api.postPenetration().then(r=>alert(r.message))}>🔍 穿透计算</button>
          <button className="btn-glass" onClick={()=>api.postRecalcCsi300().then(()=>alert('沪深300基准已更新'))}>📈 沪深300基准</button>
        </div>
      </div>

      <div className="raised" style={{marginBottom:16}}>
        <TradingCalendarView />
      </div>

      <div className="raised">
        <div className="section-title">ℹ️ 数据状态</div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:12}}>
          {[{l:'持仓',v:'44 项'},{l:'成分股',v:'72 只'},{l:'财务数据',v:'57 条'},{l:'穿透深度',v:'83 只'},{l:'沪深300',v:'已计算'},{l:'数据库',v:'PostgreSQL'}].map((d,i)=>(
            <div key={i}><div style={{fontSize:12,color:'var(--text-secondary)'}}>{d.l}</div><div style={{fontSize:16,fontWeight:600}}>{d.v}</div></div>
          ))}
        </div>
      </div>
    </div>
  )
}
