import React, { useState, useCallback, useEffect, useMemo } from 'react'
import * as api from './api'
import OverviewPanel from './components/OverviewPanel'
import AnalysisPanel from './components/AnalysisPanel'
import AnalystPanel from './components/AnalystPanel'
import TradingPanel from './components/TradingPanel'
import WatchPanel from './components/WatchPanel'
import SettingsPanel from './components/SettingsPanel'
import DataBrowser from './components/DataBrowser'
import StrategiesPanel from './components/StrategiesPanel'
import AuthGate from './components/AuthGate'
import OpsPanel from './components/OpsPanel'
import RelationPanel from './components/RelationPanel'
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
  { id: 'overview',  label: '总览',    icon: ICONS.overview, visibility: ['user','advisor','admin'] },
  { id: 'analysis',  label: '分析',    icon: ICONS.analysis, visibility: ['user','advisor','admin'] },
  { id: 'analyst',   label: '分析师',  icon: ICONS.analyst,  visibility: ['user','advisor','admin'] },
  { id: 'trading',   label: '交易',    icon: ICONS.trading,  visibility: ['user'] },
  { id: 'watch',     label: '关注',    icon: ICONS.watch,    visibility: ['user','advisor','admin'] },
  { id: 'relation',  label: '关联',    icon: 'M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a3 3 0 015.36-1.87M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 7a2 2 0 11-4 0 2 2 0 014 0z', visibility: ['user','advisor'] },
  { id: 'data',      label: '数据',    icon: ICONS.data,     visibility: ['advisor','admin'] },
  { id: 'ops',       label: '运维',    icon: 'M3 12l2-2 4 4 8-8', visibility: ['admin'] },
  { id: 'dataGap',   label: '数据补足', icon: 'M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z', visibility: ['admin'] },
  { id: 'strategies', label: 'API策略', icon: 'M13 10V3L4 14h7v7l9-11h-7z', visibility: ['admin'] },
  { id: 'settings',  label: '设置',    icon: ICONS.settings, visibility: ['user','advisor','admin'] },
]

function userRoleOf(u) {
  if (!u) return 'user'
  if (u.is_admin) return 'admin'
  if (u.is_advisor) return 'advisor'
  return 'user'
}

const TOKEN_KEY = 'portfoliom_session'
const USER_KEY = 'portfoliom_session_user'

export default function App() {
  const [sessionToken, setSessionToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [currentUser, setCurrentUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null }
  })
  const [viewAsUser, setViewAsUser] = useState(null)
  const [allUsers, setAllUsers] = useState([])
  const [activeTab, setActiveTab] = useState('overview')
  const [loading, setLoading] = useState(false)

  // 角色 + 菜单过滤
  const userRole = userRoleOf(currentUser)
  const visibleTabs = useMemo(
    () => TABS.filter(t => t.visibility.includes(userRole)),
    [userRole]
  )

  // 加载可切换用户列表（advisor/admin）
  useEffect(() => {
    if (currentUser?.is_advisor || currentUser?.is_admin) {
      api.getUsers().then(r => setAllUsers(r.users || [])).catch(() => setAllUsers([]))
    } else {
      setAllUsers([])
    }
  }, [currentUser])

  // 当 viewAs = 自己时清空
  useEffect(() => {
    if (viewAsUser && viewAsUser.id === currentUser?.id) {
      setViewAsUser(null)
    }
  }, [viewAsUser, currentUser])

  // 持久化 viewAs 到 localStorage
  useEffect(() => {
    if (viewAsUser) {
      localStorage.setItem('portfoliom_view_as', String(viewAsUser.id))
    } else {
      localStorage.removeItem('portfoliom_view_as')
    }
  }, [viewAsUser])

  // token 注入 axios
  useEffect(() => {
    if (sessionToken) {
      localStorage.setItem(TOKEN_KEY, sessionToken)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  }, [sessionToken])

  // 持久化 currentUser
  useEffect(() => {
    if (currentUser) {
      localStorage.setItem(USER_KEY, JSON.stringify(currentUser))
    } else {
      localStorage.removeItem(USER_KEY)
    }
  }, [currentUser])

  const onLoggedIn = (token, user) => {
    setSessionToken(token)
    setCurrentUser(user)
  }
  const onLogout = () => {
    setSessionToken('')
    setCurrentUser(null)
  }

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
      case 'analyst': return <AnalystPanel />
      case 'trading': return <TradingPanel />
      case 'watch': return <WatchPanel />
      case 'relation': return <RelationPanel currentUser={currentUser} />
      case 'data': return <DataBrowser />
      case 'ops': return <OpsPanel />
      case 'dataGap': return <DataGapPanel />
      case 'strategies': return <StrategiesPanel />
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
          {visibleTabs.map(tab => (
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
        <div className="sidebar-footer" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="version" style={{ fontSize: 10 }}>
              {currentUser?.display_name || currentUser?.username || '未登录'}
              {currentUser?.is_admin && <span style={{ marginLeft: 4, color: 'var(--accent)' }}>·管理员</span>}
              {currentUser?.is_advisor && !currentUser?.is_admin && <span style={{ marginLeft: 4, color: 'var(--accent)' }}>·顾问</span>}
            </span>
            <button onClick={onLogout} className="btn-ghost" style={{ padding: '2px 8px', fontSize: 10 }}>登出</button>
          </div>
          {(currentUser?.is_advisor || currentUser?.is_admin) && allUsers.length > 1 && (
            <select
              value={viewAsUser?.id || ''}
              onChange={e => {
                const id = e.target.value ? Number(e.target.value) : null
                setViewAsUser(id ? allUsers.find(u => u.id === id) : null)
              }}
              style={{ padding: '2px 4px', fontSize: 10, width: '100%' }}
              title="切换查看其他用户视图"
            >
              <option value="">查看自己</option>
              {allUsers.filter(u => u.id !== currentUser.id).map(u => (
                <option key={u.id} value={u.id}>
                  {u.display_name || u.username}
                  {u.is_admin ? ' [管]' : u.is_advisor ? ' [顾]' : ''}
                </option>
              ))}
            </select>
          )}
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <h2 className="page-title">{visibleTabs.find(t => t.id === activeTab)?.label}</h2>
          <button className="btn-ghost" onClick={refreshAll} disabled={loading}>
            {loading ? '⟳ 加载中' : '⟳ 刷新'}
          </button>
        </header>
        {viewAsUser && (
          <div style={{
            padding: '8px 16px', background: 'var(--accent-soft, #e0e7ff)',
            borderBottom: '1px solid var(--border)', display: 'flex',
            justifyContent: 'space-between', alignItems: 'center', fontSize: 12,
          }}>
            <span>
              👀 正在查看 <strong>{viewAsUser.display_name || viewAsUser.username}</strong> 的视图
              <span style={{ marginLeft: 8, color: 'var(--text-muted)', fontSize: 10 }}>
                (只读模式 — 写入仍只对 {currentUser?.display_name || currentUser?.username} 生效)
              </span>
            </span>
            <button onClick={() => setViewAsUser(null)} className="btn-ghost" style={{ fontSize: 10, padding: '2px 8px' }}>
              切回自己
            </button>
          </div>
        )}
        <div className="page-container">
          {renderPage()}
        </div>
      </main>
    </div>
  )
}