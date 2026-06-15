import React, { useState, useCallback, useEffect } from 'react'
import * as api from './api'
import OverviewPanel from './components/OverviewPanel'
import AnalysisPanel from './components/AnalysisPanel'
import TradingPanel from './components/TradingPanel'
import WatchPanel from './components/WatchPanel'
import SettingsPanel from './components/SettingsPanel'
import DataBrowser from './components/DataBrowser'
import AuthGate from './components/AuthGate'
import './App.css'

// SVG path icons (no emoji — per UI UX Pro Max §4)
const ICONS = {
  overview:  'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6',
  analysis:  'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
  analyst:   'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
  trading:   'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6',
  watch:     'M15 12a3 3 0 11-6 0 3 3 0 016 0zM2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z',
  data:      'M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4',
  settings:  'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z',
}

const TABS = [
  { id: 'overview', label: '总览', icon: ICONS.overview },
  { id: 'analysis', label: '分析', icon: ICONS.analysis },
  { id: 'analyst',  label: '分析师', icon: ICONS.analyst },
  { id: 'trading',  label: '交易', icon: ICONS.trading },
  { id: 'watch',    label: '关注', icon: ICONS.watch },
  { id: 'data',     label: '数据', icon: ICONS.data },
  { id: 'settings', label: '设置', icon: ICONS.settings },
]

const TOKEN_KEY = 'portfoliom_session'

export default function App() {
  const [sessionToken, setSessionToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [activeTab, setActiveTab] = useState('overview')
  const [loading, setLoading] = useState(false)

  // token 注入 axios
  useEffect(() => {
    if (sessionToken) {
      localStorage.setItem(TOKEN_KEY, sessionToken)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  }, [sessionToken])

  const onLoggedIn = (token) => setSessionToken(token)
  const onLogout = () => setSessionToken('')

  const refreshAll = useCallback(async () => {
    setLoading(true)
    try {
      await api.postImport()
      await api.postFillPrices()
      await api.postCrawlAll()
      await api.postPenetration()
      await api.postRecalcCsi300()
    } catch (e) { console.error(e) }
    setLoading(false)
  }, [])

  if (!sessionToken) {
    return <AuthGate onLoggedIn={onLoggedIn} />
  }

  const renderPage = () => {
    switch (activeTab) {
      case 'overview': return <OverviewPanel />
      case 'analysis': return <AnalysisPanel />
      case 'analyst': return (
        <div className="glass-card placeholder">
          <h3>📋 分析师分析</h3>
          <p>即将推出</p>
        </div>
      )
      case 'trading': return <TradingPanel />
      case 'watch': return <WatchPanel />
      case 'data': return <DataBrowser />
      case 'settings': return <SettingsPanel />
      default: return null
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="brand-icon">◆</span>
          <span className="brand-text">PortfolioM</span>
        </div>
        <nav className="sidebar-nav">
          {TABS.map(tab => (
            <button key={tab.id}
              className={`nav-item ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}>
              <span className="nav-icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d={tab.icon} />
                </svg>
              </span>
              <span className="nav-label">{tab.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="version">v0.2.0</span>
          <button onClick={onLogout} className="btn-ghost" style={{ padding: '2px 8px', fontSize: 10 }}>登出</button>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <h2 className="page-title">{TABS.find(t => t.id === activeTab)?.label}</h2>
          <button className="btn-ghost" onClick={refreshAll} disabled={loading}>
            {loading ? '⟳ 加载中' : '⟳ 刷新'}
          </button>
        </header>
        <div className="page-container">
          {renderPage()}
        </div>
      </main>
    </div>
  )
}