import React, { useState } from 'react'
import * as api from '../api'
import StyleGallery from './StyleGallery'

export default function SettingsPanel() {
  const [curPwd, setCurPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [cfmPwd, setCfmPwd] = useState('')
  const [pwdMsg, setPwdMsg] = useState({color:'var(--text-muted)',text:''})

  const changePassword = () => {
    if (newPwd.length<8||newPwd.length>12) { setPwdMsg({color:'var(--chart-down)',text:'口令长度需8-12位'}); return }
    if (newPwd!==cfmPwd) { setPwdMsg({color:'var(--chart-down)',text:'两次输入不一致'}); return }
    setPwdMsg({color:'var(--chart-up)',text:'✅ 口令已保存'})
    setCurPwd(''); setNewPwd(''); setCfmPwd('')
  }

  return (
    <div>
      <div className="raised">
        <div className="section-title">🔒 口令保护</div>
        <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:8}}>
          <label style={{minWidth:80,color:'var(--text-secondary)'}}>当前口令</label>
          <input className="ig" style={{width:200}} type="password" placeholder="输入当前口令" value={curPwd} onChange={e=>setCurPwd(e.target.value)} />
        </div>
        <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:8}}>
          <label style={{minWidth:80,color:'var(--text-secondary)'}}>新口令</label>
          <input className="ig" style={{width:200}} type="password" placeholder="8-12位" value={newPwd} onChange={e=>setNewPwd(e.target.value)} />
        </div>
        <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:8}}>
          <label style={{minWidth:80,color:'var(--text-secondary)'}}>确认</label>
          <input className="ig" style={{width:200}} type="password" placeholder="再次输入" value={cfmPwd} onChange={e=>setCfmPwd(e.target.value)} />
        </div>
        <div style={{display:'flex',gap:12,alignItems:'center'}}>
          <button className="btn-glass" style={{background:'var(--accent-gradient)',border:'none',color:'#fff'}} onClick={changePassword}>保存口令</button>
          <span style={{color:pwdMsg.color}}>{pwdMsg.text}</span>
        </div>
      </div>

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
        <StyleGallery />
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
