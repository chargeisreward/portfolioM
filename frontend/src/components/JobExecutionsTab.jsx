import React, { useState, useEffect } from 'react'
import { rawApi as api } from '../api'

/**
 * 执行监控 tab — 显示每个定时任务的本次拉取目标 / 实际拉取数量 / 拉取覆盖率。
 * 调用 GET /api/admin/data-pull-tasks（同 TaskHistoryTab，但聚焦新字段）。
 *
 * 字段：
 *   planned_count  本次拉取计划数量
 *   success_count  实际拉取有效数量
 *   coverage_rate  覆盖率 = success / planned（0.0 ~ 1.0）
 */
export default function JobExecutionsTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)

  const PAGE_SIZE = 30

  /** 拉取任务执行历史。 */
  const load = async () => {
    setLoading(true)
    try {
      const res = await api.get('/admin/data-pull-tasks', {
        params: { page, page_size: PAGE_SIZE },
      })
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch (e) {
      console.error('加载执行监控失败', e)
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [page])

  /** 覆盖率颜色：>=0.9 绿，0.6~0.9 橙，<0.6 红，无数据灰。 */
  const coverageColor = (rate) => {
    if (rate === null || rate === undefined) return 'gray'
    if (rate >= 0.9) return 'green'
    if (rate >= 0.6) return 'orange'
    return 'red'
  }

  /** 格式化覆盖率百分比。 */
  const fmtCoverage = (rate) => {
    if (rate === null || rate === undefined) return '-'
    return (rate * 100).toFixed(1) + '%'
  }

  const statusColor = { SUCCESS: 'green', FAILED: 'red', RUNNING: 'orange', SKIPPED: 'gray' }
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      <div className="raised" style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          展示每个定时任务的拉取计划数 / 实际拉取数 / 覆盖率，便于管理员快速识别数据补齐异常。
        </span>
        <button className="btn-ghost" onClick={load}>刷新</button>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>任务ID</th>
            <th>任务名</th>
            <th>开始时间</th>
            <th>状态</th>
            <th>本次拉取目标</th>
            <th>实际拉取数量</th>
            <th>覆盖率</th>
            <th>触发者</th>
            <th>错误</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr><td colSpan="9" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              {loading ? '加载中...' : '暂无数据'}
            </td></tr>
          )}
          {items.map((task, i) => (
            <tr key={task.id || i}>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{task.job_id}</td>
              <td>{task.job_name || '-'}</td>
              <td style={{ fontSize: 12 }}>{task.started_at}</td>
              <td style={{ color: statusColor[task.status] || 'inherit' }}>{task.status}</td>
              <td style={{ textAlign: 'right' }}>{task.planned_count ?? '-'}</td>
              <td style={{ textAlign: 'right' }}>{task.success_count ?? '-'}</td>
              <td style={{
                textAlign: 'right',
                color: coverageColor(task.coverage_rate),
                fontWeight: task.coverage_rate !== null && task.coverage_rate !== undefined ? 600 : 400,
              }}>
                {fmtCoverage(task.coverage_rate)}
              </td>
              <td style={{ fontSize: 12 }}>{task.triggered_by || '-'}</td>
              <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 12 }}>
                {task.error_message || '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--text-muted)' }}>
        <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>
        <span>第 {page} 页 / 共 {totalPages} 页 ({total} 条)</span>
        <button className="btn-ghost" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>下一页</button>
      </div>
    </div>
  )
}
