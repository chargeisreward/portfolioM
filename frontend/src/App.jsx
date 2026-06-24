import React, { useState, useCallback, useEffect, useMemo } from 'react'
import * as api from './api'
import OverviewPanel from './components/OverviewPanel'
import AnalysisPanel from './components/AnalysisPanel'
import AnalystPanel from './components/AnalystPanel'
import TradingPanel from './components/TradingPanel'
import WatchPanel from './components/WatchPanel'
import SettingsPanel from './components/SettingsPanel'
import AdminSettingsPanel from './components/AdminSettingsPanel'
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
  { id: 'data',      label: '数据',    icon: ICONS.data,     visibility: ['admin'] },
  { id: 'ops',       label: '运维',    icon: 'M3 12l2-2 4 4 8-8', visibility: ['admin'] },
  { id: 'dataGap',   label: '数据补足', icon: 'M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z', visibility: ['admin'] },
  { id: 'strategies', label: 'API策略', icon: 'M13 10V3L4 14h7v7l9-11h-7z', visibility: ['admin'] },
  { id: 'adminSettings', label: '管理员设置', icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z', visibility: ['admin'] },
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
  const [activeRole, setActiveRole] = useState(() => localStorage.getItem('portfoliom_active_role') || null)
  const [viewAsUser, setViewAsUser] = useState(null)
  const [allUsers, setAllUsers] = useState([])
  const [relations, setRelations] = useState({ as_advisor: [], as_client: [] })
  const [activeTab, setActiveTab] = useState('overview')
  const [loading, setLoading] = useState(false)

  // 用户最高权限角色（badge 是否有权限的依据）
  const userRole = userRoleOf(currentUser)
  // 当前激活角色（用于菜单 + 数据权限），未登录或无保存值时取最高权限角色
  const effectiveRole = activeRole && ['user','advisor','admin'].includes(activeRole) ? activeRole : userRole

  // 持久化 activeRole
  useEffect(() => {
    if (activeRole) {
      localStorage.setItem('portfoliom_active_role', activeRole)
    } else {
      localStorage.removeItem('portfoliom_active_role')
    }
  }, [activeRole])

  // 登录后初始化 activeRole 为最高权限角色
  useEffect(() => {
    if (currentUser && !activeRole) {
      setActiveRole(userRole)
    }
  }, [currentUser, activeRole, userRole])

  // 角色 + 菜单过滤（基于 effectiveRole）
  const visibleTabs = useMemo(
    () => TABS.filter(t => t.visibility.includes(effectiveRole)),
    [effectiveRole]
  )

  // 切换角色：清空 viewAs + 若当前 tab 不在新角色菜单中则回到 overview
  const switchRole = (role) => {
    setActiveRole(role)
    setViewAsUser(null)
    const allowed = TABS.filter(t => t.visibility.includes(role)).map(t => t.id)
    if (!allowed.includes(activeTab)) {
      setActiveTab('overview')
    }
  }

  // 加载所有用户列表（admin 用）+ 关联关系（advisor 用）
  useEffect(() => {
    if (currentUser?.is_admin) {
      api.getUsers().then(r => setAllUsers(r.users || [])).catch(() => setAllUsers([]))
    } else {
      setAllUsers([])
    }
    if (currentUser?.is_advisor || currentUser?.is_admin) {
      api.listRelations().then(r => setRelations(r || { as_advisor: [], as_client: [] })).catch(() => setRelations({ as_advisor: [], as_client: [] }))
    } else {
      setRelations({ as_advisor: [], as_client: [] })
    }
  }, [currentUser])

  // view_as 候选用户列表（基于 effectiveRole）
  const viewAsCandidates = useMemo(() => {
    if (effectiveRole === 'admin') {
      return allUsers.filter(u => u.id !== currentUser?.id)
    }
    if (effectiveRole === 'advisor') {
      // 从 as_advisor 关联中提取 client 用户（other_user 即 client）
      return relations.as_advisor
        .filter(r => r.status === 'ACTIVE')
        .map(r => ({
          id: r.other_user_id,
          username: r.other_username,
          display_name: r.other_display_name,
        }))
    }
    return []
  }, [effectiveRole, allUsers, relations, currentUser])

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
    setActiveRole(userRoleOf(user))
    setViewAsUser(null)
  }
  const onLogout = () => {
    setSessionToken('')
    setCurrentUser(null)
    setActiveRole(null)
    setViewAsUser(null)
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
      case 'adminSettings': return <AdminSettingsPanel />
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
        <div style={{ display: 'flex', gap: 4, padding: '0 12px 8px' }}>
          {[
            { id: 'user', label: '用户', hasPermission: !!currentUser },
            { id: 'advisor', label: '顾问', hasPermission: !!currentUser?.is_advisor },
            { id: 'admin', label: '管理员', hasPermission: !!currentUser?.is_admin },
          ].map(b => {
            const isActive = effectiveRole === b.id
            const clickable = b.hasPermission
            return (
              <button
                key={b.id}
                disabled={!clickable}
                onClick={() => clickable && switchRole(b.id)}
                style={{
                  flex: 1, textAlign: 'center', padding: '3px 0', borderRadius: 3,
                  fontSize: 9, fontFamily: '"GeistMono", monospace', letterSpacing: 0.3,
                  cursor: clickable ? 'pointer' : 'default',
                  transition: 'all 0.15s',
                  background: isActive ? 'var(--up)' : (clickable ? 'transparent' : 'var(--bg-raised)'),
                  color: isActive ? '#fff' : (clickable ? 'var(--up)' : 'var(--text-muted)'),
                  border: `1px solid ${isActive ? 'var(--up)' : (clickable ? 'var(--up)' : 'var(--border)')}`,
                  opacity: clickable && !isActive ? 0.7 : 1,
                }}
              >
                {b.label}
              </button>
            )
          })}
        </div>
        {(effectiveRole === 'advisor' || effectiveRole === 'admin') && viewAsCandidates.length > 0 && (
          <div style={{ padding: '0 12px 8px' }}>
            <select
              value={viewAsUser?.id || ''}
              onChange={e => {
                const id = e.target.value ? Number(e.target.value) : null
                setViewAsUser(id ? viewAsCandidates.find(u => u.id === id) : null)
              }}
              style={{
                width: '100%', padding: '4px 6px', fontSize: 10,
                background: 'var(--bg-raised)', color: 'var(--text)',
                border: '1px solid var(--border)', borderRadius: 3,
                cursor: 'pointer',
              }}
              title={effectiveRole === 'admin' ? '选择用户查看数据（管理员）' : '选择客户查看数据（顾问）'}
            >
              <option value="">{effectiveRole === 'admin' ? '查看自己（全部）' : '查看自己'}</option>
              {viewAsCandidates.map(u => (
                <option key={u.id} value={u.id}>
                  {u.display_name || u.username}
                  {u.is_admin ? ' [管]' : u.is_advisor ? ' [顾]' : ''}
                </option>
              ))}
            </select>
          </div>
        )}
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