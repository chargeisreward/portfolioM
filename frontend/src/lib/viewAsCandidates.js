/**
 * 计算视图代理候选用户列表（viewAsCandidates）
 *
 * 业务规则：
 * - admin 角色：返回所有用户（除当前用户自己），因为管理员可以查看任意用户
 * - advisor 角色：返回与该顾问建立 ACTIVE 关联的客户列表
 * - 其他角色（user / null）：返回空数组
 *
 * @param {string} dataRole - 数据角色（activeRole || userRole），不因 viewAsUser 降级
 * @param {Array} allUsers - 所有用户列表
 * @param {{as_advisor?: Array, as_client?: Array}} relations - 关联关系
 * @param {{id?: number}} currentUser - 当前登录用户
 * @returns {Array} 候选用户列表
 */
export function calcViewAsCandidates(dataRole, allUsers, relations, currentUser) {
  if (dataRole === 'admin') {
    return allUsers.filter(u => u.id !== currentUser?.id)
  }
  if (dataRole === 'advisor') {
    return (relations.as_advisor || [])
      .filter(r => r.status === 'ACTIVE')
      .map(r => ({
        id: r.other_user_id,
        username: r.other_username,
        display_name: r.other_display_name,
      }))
  }
  return []
}

/**
 * 计算数据角色（dataRole）
 *
 * dataRole = activeRole || userRole
 * - activeRole：用户手动切换的 badge 角色（可能为 null）
 * - userRole：当前用户最高权限角色（admin > advisor > user）
 *
 * dataRole 不因 viewAsUser 降级（用于 viewAsCandidates 计算）
 *
 * @param {string|null} activeRole - 用户手动切换的角色
 * @param {string} userRole - 当前用户最高权限角色
 * @returns {string} 数据角色
 */
export function calcDataRole(activeRole, userRole) {
  return activeRole || userRole
}
