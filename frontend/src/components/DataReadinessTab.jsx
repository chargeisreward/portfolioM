import React, { useState, useEffect } from 'react'
import { rawApi as api } from '../api'

/**
 * 数据就绪 tab — 按业务日期展示各数据源的就绪状态。
 * 调用 GET /api/admin/data-readiness?as_of_date=YYYY-MM-DD。
 */
export default function DataReadinessTab() {
  const [asOf, setAsOf] = useState(new Date().toISOString().slice(0, 10))
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)

  /** 拉取数据就绪状态。 */
  const load = async () => {
    setLoading(true)
    try {
      const res = await api.get('/admin/data-readiness', { params: { as_of_date: asOf } })
      setItems(res.data.items || [])
    } catch (e) {
      console.error('加载数据就绪状态失败', e)
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const statusIcon = { ok: '✅', missing: '❌', partial: '⚠️' }

  return (
    <div>
      {/* 日期选择 + 查询 */}
      <div className="raised" style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          业务日期
          <input
            type="date"
            className="ig"
            value={asOf}
            onChange={e => setAsOf(e.target.value)}
            style={{ marginLeft: 6 }}
          />
        </label>
        <button className="btn-ghost" onClick={load}>查询</button>
      </div>

      {/* 就绪状态表格 */}
      <table className="data-table">
        <thead>
          <tr>
            <th>数据源</th>
            <th>期望记录数</th>
            <th>实际记录数</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr><td colSpan="4" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              {loading ? '加载中...' : '暂无数据'}
            </td></tr>
          )}
          {items.map((item, i) => (
            <tr key={i}>
              <td>{item.source}</td>
              <td>{item.expected}</td>
              <td>{item.actual}</td>
              <td>{statusIcon[item.status] || ''} {item.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
