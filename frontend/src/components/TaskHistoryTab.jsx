import React, { useState, useEffect } from 'react'
import { rawApi as api } from '../api'

/**
 * 任务历史 tab — 显示数据拉取任务执行记录 + 手动触发。
 * 调用 GET /api/admin/data-pull-tasks + POST /api/admin/data-pull-tasks/trigger/{job_id}。
 */
export default function TaskHistoryTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(false)

  const PAGE_SIZE = 50

  /** 拉取任务历史列表。 */
  const load = async () => {
    setLoading(true)
    try {
      const params = { page, page_size: PAGE_SIZE }
      if (statusFilter) params.status = statusFilter
      const res = await api.get('/admin/data-pull-tasks', { params })
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch (e) {
      console.error('加载任务历史失败', e)
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [page, statusFilter])

  /** 手动触发任务。 */
  const handleTrigger = async (jobId) => {
    try {
      const res = await api.post(`/admin/data-pull-tasks/trigger/${jobId}`)
      alert(`触发成功：${res.data.message || res.data.status}`)
      setTimeout(load, 2000)  // 2 秒后刷新
    } catch (e) {
      alert('触发失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  // 常见 job_id 列表（对应 scheduler.JOB_DISPATCH）
  const knownJobs = [
    { id: 'realtime_prices', name: '实时价格' },
    { id: 'drill_snapshot', name: '下钻快照' },
    { id: 'financial_fundamentals', name: '财务数据' },
    { id: 'detect_data_gaps', name: '数据缺口检测' },
    { id: 'backfill_gaps', name: '回填缺口' },
    { id: 'pull_fund_nav', name: '基金净值' },
  ]

  const statusColor = { SUCCESS: 'green', FAILED: 'red', RUNNING: 'orange', SKIPPED: 'gray' }
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      {/* 筛选 + 刷新 + 手动触发 */}
      <div className="raised" style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="ig" value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1) }}>
          <option value="">全部状态</option>
          <option value="SUCCESS">成功</option>
          <option value="FAILED">失败</option>
          <option value="RUNNING">运行中</option>
          <option value="SKIPPED">跳过</option>
        </select>
        <button className="btn-ghost" onClick={load}>刷新</button>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>手动触发：</span>
        {knownJobs.map(job => (
          <button key={job.id} className="btn-ghost" onClick={() => handleTrigger(job.id)}>
            {job.name}
          </button>
        ))}
      </div>

      {/* 任务历史表格 */}
      <table className="data-table">
        <thead>
          <tr>
            <th>任务ID</th>
            <th>任务名</th>
            <th>开始时间</th>
            <th>结束时间</th>
            <th>状态</th>
            <th>记录数</th>
            <th>触发者</th>
            <th>错误</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr><td colSpan="8" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              {loading ? '加载中...' : '暂无数据'}
            </td></tr>
          )}
          {items.map((task, i) => (
            <tr key={task.id || i}>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{task.job_id}</td>
              <td>{task.job_name}</td>
              <td>{task.started_at}</td>
              <td>{task.finished_at || '-'}</td>
              <td style={{ color: statusColor[task.status] }}>{task.status}</td>
              <td>{task.records_pulled}</td>
              <td>{task.triggered_by}</td>
              <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {task.error_message || '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* 分页 */}
      <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--text-muted)' }}>
        <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>
        <span>第 {page} 页 / 共 {totalPages} 页 ({total} 条)</span>
        <button className="btn-ghost" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>下一页</button>
      </div>
    </div>
  )
}
