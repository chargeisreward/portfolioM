import React, { useEffect, useState } from 'react'
import {
  listRelations, createRelation, confirmRelation, cancelRelation, getUsers,
} from '../api'

/**
 * 关联管理 — 用户可下拉选择顾问；顾问可下拉选择客户
 * 双方都选 → 关联建立（PENDING→对方 confirm→ACTIVE）
 * 任一方取消 → CANCELLED
 */
export default function RelationPanel({ currentUser }) {
  const [data, setData] = useState({ as_advisor: [], as_client: [] })
  const [users, setUsers] = useState([])
  const [inviteTarget, setInviteTarget] = useState('')
  const [busy, setBusy] = useState(false)

  function refresh() {
    listRelations().then(setData).catch(() => setData({ as_advisor: [], as_client: [] }))
  }
  useEffect(refresh, [])

  // 加载可邀请目标列表
  useEffect(() => {
    if (!currentUser) return
    getUsers().then(r => setUsers(r.users || []))
  }, [currentUser])

  const isAdvisor = currentUser?.is_advisor
  const isAdmin = currentUser?.is_admin
  const canInviteClient = isAdvisor || isAdmin
  // 候选（已排除自己和已存在关联的对方）
  const existingOthers = new Set([
    ...data.as_advisor.map(r => r.other_user_id),
    ...data.as_client.map(r => r.other_user_id),
  ])
  const candidates = users.filter(u => {
    if (u.id === currentUser?.id) return false
    if (existingOthers.has(u.id)) return false
    if (canInviteClient) return true
    // 普通 user 只能邀请顾问
    return u.is_advisor
  })

  async function invite() {
    if (!inviteTarget) return
    setBusy(true)
    try {
      const body = canInviteClient
        ? { client_username: inviteTarget }
        : { advisor_username: inviteTarget }
      const r = await createRelation(body)
      setInviteTarget('')
      refresh()
      if (r.status === 'created' || r.status === 'recreated') {
        alert('已发送邀请；等待对方确认。')
      } else if (r.status === 'exists') {
        alert('已存在关联（' + (r.current_status || '') + '）。')
      }
    } catch (e) {
      alert(e?.response?.data?.detail || '发起失败')
    }
    setBusy(false)
  }

  async function act(rel, action, label) {
    if (!confirm(`确认${label}关联 #${rel.id}？`)) return
    try {
      await action(rel.id)
      refresh()
    } catch (e) {
      alert(e?.response?.data?.detail || '操作失败')
    }
  }

  const allRels = [
    ...data.as_advisor.map(r => ({ ...r, direction: '作为客户' })),
    ...data.as_client.map(r => ({ ...r, direction: '作为顾问' })),
  ]

  return (
    <div style={{ padding: 24 }}>
      <h2>关联管理</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 12, marginBottom: 16 }}>
        {canInviteClient
          ? '邀请客户建立关联；客户确认后即可查看其数据。'
          : '选择你的顾问；顾问确认后即可建立关联。'}
      </p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
        <select
          value={inviteTarget}
          onChange={e => setInviteTarget(e.target.value)}
          style={{ padding: '6px 10px', minWidth: 200 }}
          disabled={busy}
        >
          <option value="">
            {canInviteClient ? '选择客户...' : '选择顾问...'}
          </option>
          {candidates.map(u => (
            <option key={u.id} value={u.username}>
              {u.display_name || u.username}
              {u.is_admin ? ' [管理员]' : u.is_advisor ? ' [顾问]' : ''}
            </option>
          ))}
        </select>
        <button onClick={invite} disabled={!inviteTarget || busy}>
          {busy ? '发送中...' : '发起邀请'}
        </button>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
            <th style={{ padding: 8 }}>方向</th>
            <th style={{ padding: 8 }}>对方</th>
            <th style={{ padding: 8 }}>状态</th>
            <th style={{ padding: 8 }}>发起方</th>
            <th style={{ padding: 8 }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {allRels.length === 0 && (
            <tr><td colSpan="5" style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)' }}>
              暂无关联
            </td></tr>
          )}
          {allRels.map(r => (
            <tr key={r.id} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: 8 }}>{r.direction}</td>
              <td style={{ padding: 8 }}>{r.other_display_name || r.other_username}</td>
              <td style={{ padding: 8 }}>
                <span style={{
                  color: r.status === 'ACTIVE' ? 'var(--up, #16a34a)'
                    : r.status === 'CANCELLED' ? 'var(--down, #dc2626)'
                    : 'var(--text-muted)',
                }}>
                  {r.status}
                </span>
              </td>
              <td style={{ padding: 8 }}>
                {r.initiator_user_id === currentUser?.id ? '我发起' : '对方发起'}
              </td>
              <td style={{ padding: 8, display: 'flex', gap: 4 }}>
                {r.status === 'PENDING' && r.initiator_user_id !== currentUser?.id && (
                  <button onClick={() => act(r, confirmRelation, '确认')}
                    style={{ padding: '2px 8px', fontSize: 11 }}>
                    确认
                  </button>
                )}
                {r.status !== 'CANCELLED' && (
                  <button onClick={() => act(r, cancelRelation, '取消')}
                    style={{ padding: '2px 8px', fontSize: 11 }}>
                    取消
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
