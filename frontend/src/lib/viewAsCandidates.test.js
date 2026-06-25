import { describe, it, expect } from 'vitest'
import { calcViewAsCandidates, calcDataRole } from './viewAsCandidates.js'

// 测试数据：模拟后端 /api/auth/users 返回的 5 个用户
const ALL_USERS = [
  { id: 1, username: 'admin',   display_name: '系统管理员', is_admin: true,  is_advisor: false },
  { id: 2, username: 'advisor', display_name: '李 顾问',    is_admin: false, is_advisor: true  },
  { id: 3, username: 'user',    display_name: '王用户',     is_admin: false, is_advisor: false },
  { id: 4, username: 'user_b',  display_name: '李女士',     is_admin: false, is_advisor: false },
  { id: 5, username: 'user_c',  display_name: '赵客户',     is_admin: false, is_advisor: false },
]

const ADMIN_USER = { id: 1, username: 'admin', is_admin: true }
const ADVISOR_USER = { id: 2, username: 'advisor', is_advisor: true }
const NORMAL_USER = { id: 3, username: 'user' }

describe('calcDataRole', () => {
  it('activeRole 为 null 时，dataRole = userRole', () => {
    expect(calcDataRole(null, 'admin')).toBe('admin')
    expect(calcDataRole(null, 'advisor')).toBe('advisor')
    expect(calcDataRole(null, 'user')).toBe('user')
  })

  it('activeRole 有值时，dataRole = activeRole', () => {
    expect(calcDataRole('admin', 'admin')).toBe('admin')
    expect(calcDataRole('user', 'admin')).toBe('user') // admin 切换到 user badge
  })
})

describe('calcViewAsCandidates — admin 角色', () => {
  it('admin 登录后应返回除自己外的所有用户（4 个）', () => {
    const result = calcViewAsCandidates('admin', ALL_USERS, { as_advisor: [] }, ADMIN_USER)
    expect(result).toHaveLength(4)
    expect(result.map(u => u.id)).toEqual([2, 3, 4, 5])
  })

  it('admin 候选列表不应包含 admin 自己', () => {
    const result = calcViewAsCandidates('admin', ALL_USERS, { as_advisor: [] }, ADMIN_USER)
    expect(result.find(u => u.id === 1)).toBeUndefined()
  })

  it('allUsers 为空时返回空数组', () => {
    const result = calcViewAsCandidates('admin', [], { as_advisor: [] }, ADMIN_USER)
    expect(result).toEqual([])
  })

  it('currentUser 为 null 时返回所有用户（无法过滤自己）', () => {
    const result = calcViewAsCandidates('admin', ALL_USERS, { as_advisor: [] }, null)
    expect(result).toHaveLength(5)
  })
})

describe('calcViewAsCandidates — advisor 角色', () => {
  it('advisor 应看到 ACTIVE 关联的客户', () => {
    const relations = {
      as_advisor: [
        { status: 'ACTIVE', other_user_id: 3, other_username: 'user',    other_display_name: '王用户' },
        { status: 'ACTIVE', other_user_id: 4, other_username: 'user_b',  other_display_name: '李女士' },
        { status: 'PENDING', other_user_id: 5, other_username: 'user_c', other_display_name: '赵客户' },
      ],
    }
    const result = calcViewAsCandidates('advisor', ALL_USERS, relations, ADVISOR_USER)
    expect(result).toHaveLength(2)
    expect(result[0].id).toBe(3)
    expect(result[1].id).toBe(4)
  })

  it('无 ACTIVE 关联时返回空数组', () => {
    const relations = { as_advisor: [{ status: 'PENDING', other_user_id: 3 }] }
    const result = calcViewAsCandidates('advisor', ALL_USERS, relations, ADVISOR_USER)
    expect(result).toEqual([])
  })
})

describe('calcViewAsCandidates — user 角色', () => {
  it('普通用户返回空数组（无权查看其他用户）', () => {
    const result = calcViewAsCandidates('user', ALL_USERS, { as_advisor: [] }, NORMAL_USER)
    expect(result).toEqual([])
  })

  it('dataRole 为 null 时返回空数组', () => {
    const result = calcViewAsCandidates(null, ALL_USERS, { as_advisor: [] }, NORMAL_USER)
    expect(result).toEqual([])
  })
})
