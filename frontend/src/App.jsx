import React, { useState, useCallback, useEffect, useMemo } from 'react'
import * as api from './api'
import { calcViewAsCandidates, calcDataRole } from './lib/viewAsCandidates'
import OverviewPanel from './components/OverviewPanel'
import AnalysisPanel from './components/AnalysisPanel'
import AnalystPanel from './components/AnalystPanel'
import TradingPanel from './components/TradingPanel'
import ValuationPanel from './components/ValuationPanel'
import WatchPanel from './components/WatchPanel'
import SettingsPanel from './components/SettingsPanel'
import AuthGate from './components/AuthGate'
import RelationPanel from './components/RelationPanel'
import MasterDataPanel from './components/MasterDataPanel'
import DataSourcePanel from './components/DataSourcePanel'
import ContentUploadPanel from './components/ContentUploadPanel'
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
  { id: 'watch',     label: '关注',    icon: ICONS.watch,    visibility: ['user','advisor','admin'] },
  { id: 'trading',   label: '交易',    icon: ICONS.trading,  visibility: ['user'] },
  { id: 'valuation', label: '估值表',  icon: 'M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z', visibility: ['user','advisor','admin'] },
  { id: 'relation',  label: '关联',    icon: 'M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a3 3 0 015.36-1.87M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 7a2 2 0 11-4 0 2 2 0 014 0z', visibility: ['user','advisor'] },
  { id: 'settings',  label: '设置',    icon: ICONS.settings, visibility: ['user','advisor','admin'] },
  // --- 分割线（仅 admin 可见） ---
  { id: 'masterData',   label: '主数据',   icon: 'M4 6h16M4 12h16M4 18h7', visibility: ['admin'] },
  { id: 'dataSource',   label: '数据源',   icon: 'M4 7v10m4-14v18m4-14v10m4-14v18', visibility: ['admin'] },
  { id: 'contentUpload', label: '内容上传', icon: 'M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2z', visibility: ['admin'] },
]

function userRoleOf(u) {
  if (!u) return 'user'
  if (u.is_admin) return 'admin'
  if (u.is_advisor) return 'advisor'
  return 'user'
}

export default function App() {
  // 认证状态：currentUser 存在即已登录（token 由 HttpOnly cookie 管理，JS 不可读）
  const [currentUser, setCurrentUser] = useState(null)
  // UI 状态：不持久化到 localStorage，刷新后重置为默认值
  const [activeRole, setActiveRole] = useState(null)
  const [viewAsUser, setViewAsUser] = useState(null)
  const [allUsers, setAllUsers] = useState([])
  const [relations, setRelations] = useState({ as_advisor: [], as_client: [] })
  const [activeTab, setActiveTab] = useState('overview')
  // 主数据页"缺指数构成"卡片跳转到内容上传页时预选的指数代码
  const [pendingIndexUpload, setPendingIndexUpload] = useState(null)
  const [loading, setLoading] = useState(false)
  // 启动时总是验证 cookie（不再读 localStorage 判断是否需要验证）
  const [validating, setValidating] = useState(true)

  // 用户最高权限角色（badge 是否有权限的依据）
  const userRole = userRoleOf(currentUser)
  // 当前激活角色（用于菜单 + 数据权限），未登录或无保存值时取最高权限角色
  // 管理员选择了 viewAsUser 时，菜单降级为 user（等同顾问查看客户的菜单权限）
  const effectiveRole = useMemo(() => {
    if (activeRole && ['user','advisor','admin'].includes(activeRole)) {
      if (activeRole === 'admin' && viewAsUser) return 'user'
      return activeRole
    }
    return userRole
  }, [activeRole, viewAsUser, userRole])
  // 数据角色（用于 viewAsCandidates，不因 viewAsUser 降级）
  const dataRole = calcDataRole(activeRole, userRole)

  // 启动时验证 cookie 有效性（HttpOnly cookie 由浏览器自动携带，JS 不可读）
  // 总是调用 /auth/me：有有效 cookie → 登录态；无 → 显示 AuthGate
  useEffect(() => {
    setValidating(true)
    api.getAuthMe()
      .then(user => {
        if (user && user.id) {
          setCurrentUser(user)
          setActiveRole(userRoleOf(user))
        } else {
          setCurrentUser(null)
          setActiveRole(null)
        }
      })
      .catch(() => {
        // cookie 无效或不存在 — 显示 AuthGate
        setCurrentUser(null)
        setActiveRole(null)
      })
      .finally(() => setValidating(false))
  }, []) // 仅挂载时执行一次

  // 注册 401 回调：API 返回 401 时重置状态显示 AuthGate
  useEffect(() => {
    api.onUnauthorized(() => {
      setCurrentUser(null)
      setActiveRole(null)
      setViewAsUser(null)
    })
  }, [])

  // viewAsUser 变化时同步到 api 模块（内存变量，不持久化）
  useEffect(() => {
    api.setViewAs(viewAsUser?.id || null)
  }, [viewAsUser])

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

  // view_as 候选用户列表（基于 dataRole，不受 viewAsUser 影响）
  const viewAsCandidates = useMemo(
    () => calcViewAsCandidates(dataRole, allUsers, relations, currentUser),
    [dataRole, allUsers, relations, currentUser]
  )

  // 当 viewAs = 自己时清空
  useEffect(() => {
    if (viewAsUser && viewAsUser.id === currentUser?.id) {
      setViewAsUser(null)
    }
  }, [viewAsUser, currentUser])

  // viewAsUser 变化导致 effectiveRole 变化时，检查当前 tab 是否仍有效
  useEffect(() => {
    const allowed = TABS.filter(t => t.visibility.includes(effectiveRole)).map(t => t.id)
    if (!allowed.includes(activeTab)) {
      setActiveTab('overview')
    }
  }, [effectiveRole, activeTab])

  // 登录成功回调：token 由后端 Set-Cookie 管理，前端只更新用户状态
  const onLoggedIn = (token, user) => {
    setCurrentUser(user)
    setActiveRole(userRoleOf(user))
    setViewAsUser(null)
  }

  // 登出：调后端清除 cookie + 重置前端状态
  const onLogout = async () => {
    try { await api.logout() } catch { /* ignore */ }
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
      // 触发 trend 三级回退自愈（force=True，360 天口径），完成后通知 OverviewPanel 刷新走势图
      try {
        await api.getTrend(360, 'CNY', true)
        window.dispatchEvent(new CustomEvent('trend-healed'))
      } catch (e) { console.error('trend heal failed:', e) }
    } catch (e) { console.error(e) }
    setLoading(false)
  }, [])

  if (validating) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <div style={{ color: 'var(--text-muted, #888)', fontSize: 14 }}>验证登录状态...</div>
      </div>
    )
  }

  // currentUser 存在即已登录（cookie 由浏览器管理）；不存在则显示登录门
  if (!currentUser) {
    return <AuthGate onLoggedIn={onLoggedIn} />
  }

  const renderPage = () => {
    switch (activeTab) {
      case 'overview': return <OverviewPanel />
      case 'analysis': return <AnalysisPanel />
      case 'analyst': return <AnalystPanel />
      case 'trading': return <TradingPanel />
      case 'valuation': return <ValuationPanel />
      case 'watch': return <WatchPanel />
      case 'relation': return <RelationPanel currentUser={currentUser} />
      case 'settings': return <SettingsPanel />
      case 'masterData': return <MasterDataPanel onMissingConstituents={(idx) => {
        setPendingIndexUpload(idx)
        setActiveTab('contentUpload')
      }} />
      case 'dataSource': return <DataSourcePanel />
      case 'contentUpload': return <ContentUploadPanel preSelectIndex={pendingIndexUpload} />
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
        {/* 角色切换三连 badge — monochrome brutalist 风格 */}
        <div style={{ display: 'flex', gap: 4, padding: '0 12px 8px' }}>
          {[
            { id: 'user', label: '用户', hasPermission: !!currentUser },
            { id: 'advisor', label: '顾问', hasPermission: !!currentUser?.is_advisor },
            { id: 'admin', label: '管理员', hasPermission: !!currentUser?.is_admin },
          ].map(b => {
            const isActive = effectiveRole === b.id
            const clickable = b.hasPermission
            const cls = `role-badge${isActive ? ' on' : ''}${!clickable ? ' is-disabled' : ''}`
            return (
              <button
                key={b.id}
                disabled={!clickable}
                onClick={() => clickable && switchRole(b.id)}
                className={cls}
                title={clickable ? `切换到${b.label}视角` : `无${b.label}权限`}
              >
                {b.label}
              </button>
            )
          })}
        </div>
        {(dataRole === 'advisor' || dataRole === 'admin') && viewAsCandidates.length > 0 && (
          <div style={{ padding: '0 12px 8px' }}>
            <div style={{
              fontSize: 9, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace',
              letterSpacing: 0.8, textTransform: 'uppercase', marginBottom: 3, paddingLeft: 2,
            }}>
              {dataRole === 'admin' ? '视角 · 用户' : '视角 · 客户'}
            </div>
            <select
              className="viewas-select"
              value={viewAsUser?.id || ''}
              onChange={e => {
                const id = e.target.value ? Number(e.target.value) : null
                setViewAsUser(id ? viewAsCandidates.find(u => u.id === id) : null)
              }}
              title={dataRole === 'admin' ? '选择用户查看数据（管理员）' : '选择客户查看数据（顾问）'}
            >
              <option value="">
                {dataRole === 'admin' ? '选择用户…' : '选择客户…'}
              </option>
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
          {visibleTabs.map(tab => {
            // masterData 之前插入灰色分割线（仅 admin 可见，因 masterData 只对 admin 可见）
            const showDivider = tab.id === 'masterData'
            return (
              <React.Fragment key={tab.id}>
                {showDivider && <div className="sidebar-divider" style={{height:1, background:'var(--border, #ccc)', margin:'8px 0'}} />}
                <button className={`nav-item ${activeTab === tab.id ? 'active' : ''}`}
                  onClick={() => setActiveTab(tab.id)}>
                  <span className="nav-icon">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d={tab.icon} />
                    </svg>
                  </span>
                  <span className="nav-label">{tab.label}</span>
                </button>
              </React.Fragment>
            )
          })}
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
          <div className="viewas-banner">
            <span>
              <span style={{ color: 'var(--text-muted)', marginRight: 6 }}>↗</span>
              正在查看 <strong>{viewAsUser.display_name || viewAsUser.username}</strong> 的视图
              <span style={{ marginLeft: 8, color: 'var(--text-muted)', fontSize: 10 }}>
                (只读 — 写入仍对 {currentUser?.display_name || currentUser?.username} 生效)
              </span>
            </span>
            <button onClick={() => setViewAsUser(null)} className="btn-ghost" style={{ fontSize: 10, padding: '2px 8px' }}>
              切回自己
            </button>
          </div>
        )}
        {/* key 绑定 viewAsUser：切换视角时强制重新挂载子组件，触发所有 useEffect 重新获取数据 */}
        <div className="page-container" key={viewAsUser?.id || 'self'}>
          {renderPage()}
        </div>
      </main>
    </div>
  )
}