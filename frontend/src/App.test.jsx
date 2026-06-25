import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
// mock api 模块（避免真实网络请求）
vi.mock('./api', () => ({
  getAuthMe: vi.fn(),
  getUsers: vi.fn(),
  listRelations: vi.fn(),
  getAuthStatus: vi.fn().mockResolvedValue({ banned: false }),
  login: vi.fn(),
  logout: vi.fn().mockResolvedValue({ status: 'ok' }),
  rawApi: { get: vi.fn(), post: vi.fn() },
  setViewAs: vi.fn(),
  onUnauthorized: vi.fn(),
}))

// mock 所有 Panel 组件（避免渲染复杂子树）
vi.mock('./components/OverviewPanel', () => ({ default: () => null }))
vi.mock('./components/AnalysisPanel', () => ({ default: () => null }))
vi.mock('./components/AnalystPanel', () => ({ default: () => null }))
vi.mock('./components/TradingPanel', () => ({ default: () => null }))
vi.mock('./components/WatchPanel', () => ({ default: () => null }))
vi.mock('./components/SettingsPanel', () => ({ default: () => null }))
vi.mock('./components/RelationPanel', () => ({ default: () => null }))
vi.mock('./components/MasterDataPanel', () => ({ default: () => null }))
vi.mock('./components/DataSourcePanel', () => ({ default: () => null }))
vi.mock('./components/ContentUploadPanel', () => ({ default: () => null }))

import App from './App'
import * as api from './api'

// 测试数据：模拟后端 /api/auth/users 返回的 5 个用户
const ALL_USERS = [
  { id: 1, username: 'admin',   display_name: '系统管理员', is_admin: true,  is_advisor: false },
  { id: 2, username: 'advisor', display_name: '李 顾问',    is_admin: false, is_advisor: true  },
  { id: 3, username: 'user',    display_name: '王用户',     is_admin: false, is_advisor: false },
  { id: 4, username: 'user_b',  display_name: '李女士',     is_admin: false, is_advisor: false },
  { id: 5, username: 'user_c',  display_name: '赵客户',     is_admin: false, is_advisor: false },
]

const ADMIN_USER = { id: 1, username: 'admin', display_name: '系统管理员', is_admin: true, is_advisor: false }

describe('App — admin 下拉菜单（cookie 认证）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  /**
   * 核心业务测试：admin 登录后下拉菜单应显示所有用户
   * 认证方式改为 HttpOnly cookie，App 启动时调 /auth/me 验证
   */
  it('admin 登录后，下拉菜单应显示所有用户（除自己）', async () => {
    // mock /auth/me 返回 admin（模拟 cookie 有效）
    api.getAuthMe.mockResolvedValue(ADMIN_USER)
    api.getUsers.mockResolvedValue({ users: ALL_USERS })
    api.listRelations.mockResolvedValue({ as_advisor: [], as_client: [] })

    const { container } = render(<App />)

    // 等待 getUsers 被调用并完成
    await waitFor(() => {
      expect(api.getUsers).toHaveBeenCalled()
    })

    // 等待 allUsers 加载完成（select 出现）
    await waitFor(() => {
      const select = container.querySelector('select')
      expect(select).not.toBeNull()
    }, { timeout: 3000 })

    const select = container.querySelector('select')
    expect(select).not.toBeNull()

    // 验证 option 数量：1 个"查看自己" + 4 个用户 = 5 个
    const options = select.querySelectorAll('option')
    expect(options).toHaveLength(5)

    // 验证第一个 option 是"查看自己（管理员）"
    expect(options[0].textContent).toBe('查看自己（管理员）')

    // 验证其他 option 包含用户名
    const optionTexts = Array.from(options).map(o => o.textContent)
    expect(optionTexts).toContain('李 顾问 [顾]')
    expect(optionTexts).toContain('王用户')
    expect(optionTexts).toContain('李女士')
    expect(optionTexts).toContain('赵客户')
  })

  /**
   * 安全性测试：验证 App 不再使用 localStorage 存储敏感信息
   * 登录成功后不应写 localStorage（token 由 HttpOnly cookie 管理）
   */
  it('admin 登录流程不应写入 localStorage（token 由 cookie 管理）', async () => {
    api.getAuthMe.mockResolvedValue(ADMIN_USER)
    api.getUsers.mockResolvedValue({ users: ALL_USERS })
    api.listRelations.mockResolvedValue({ as_advisor: [], as_client: [] })

    const { container } = render(<App />)

    await waitFor(() => {
      const select = container.querySelector('select')
      expect(select).not.toBeNull()
    }, { timeout: 3000 })

    // 验证 localStorage 中没有敏感数据
    expect(localStorage.getItem('portfoliom_session')).toBeNull()
    expect(localStorage.getItem('portfoliom_session_user')).toBeNull()
    expect(localStorage.getItem('portfoliom_active_role')).toBeNull()
    expect(localStorage.getItem('portfoliom_view_as')).toBeNull()
  })

  /**
   * 验证 cookie 无效时显示 AuthGate
   */
  it('cookie 无效时（/auth/me 抛 401），应显示登录页', async () => {
    api.getAuthMe.mockRejectedValue(new Error('401'))

    const { container } = render(<App />)

    await waitFor(() => {
      // AuthGate 渲染登录表单（input username）
      const input = container.querySelector('input[autoComplete="username"]')
      expect(input).not.toBeNull()
    }, { timeout: 3000 })
  })
})
