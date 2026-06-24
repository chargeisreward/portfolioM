import React, { useState } from 'react'

/**
 * 设置面板 — 仅包含密码变更（所有用户可见）
 * 数据管理 / 交易日历等管理功能已移至 AdminSettingsPanel
 */
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
    </div>
  )
}
