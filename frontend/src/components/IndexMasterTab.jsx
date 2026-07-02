import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

const CATEGORIES = ['宽基', '行业', '主题', '策略']
const SOURCES = ['akshare', 'manual', 'manual_qqq_seed', 'manual_legacy']

/**
 * 指数主数据 tab。
 * 含「手动刷新」按钮 → POST /admin/index-master/refresh 触发 akshare 轮询。
 */
export default function IndexMasterTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ category: '', is_active: '', search: '' })
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null)
  const [refreshMsg, setRefreshMsg] = useState('')
  const PAGE_SIZE = 50

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: PAGE_SIZE }
      if (filters.category) params.category = filters.category
      if (filters.is_active !== '') params.is_active = filters.is_active
      if (filters.search) params.search = filters.search
      const res = await api.get('/admin/index-master', { params })
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch (e) {
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [page, filters])

  useEffect(() => { load() }, [load])

  const handleSave = async (data) => {
    try {
      if (editing.index_code) {
        await api.put(`/admin/index-master/${encodeURIComponent(editing.index_code)}`, data)
      } else {
        await api.post('/admin/index-master', data)
      }
      setEditing(null)
      load()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  const handleRefresh = async () => {
    setRefreshMsg('正在拉取...')
    try {
      const res = await api.post('/admin/index-master/refresh')
      const { inserted = 0, updated = 0, marked_inactive = 0 } = res.data || {}
      setRefreshMsg(`刷新完成: 新增 ${inserted}, 更新 ${updated}, 标记下架 ${marked_inactive}`)
      load()
    } catch (e) {
      setRefreshMsg('刷新失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  return (
    <div className="raised" style={{ padding: 12 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <input className="ig" placeholder="搜索代码/名称"
               value={filters.search}
               onChange={(e) => setFilters({...filters, search: e.target.value, page: 1})} />
        <select className="ig" value={filters.category}
                onChange={(e) => setFilters({...filters, category: e.target.value, page: 1})}>
          <option value="">全部分类</option>
          {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="ig" value={filters.is_active}
                onChange={(e) => setFilters({...filters, is_active: e.target.value, page: 1})}>
          <option value="">全部状态</option>
          <option value="true">启用中</option>
          <option value="false">已下架</option>
        </select>
        <button className="btn-ghost" onClick={handleRefresh}>akshare 手动刷新</button>
        <button className="btn-ghost" onClick={() => setEditing({})}>+ 新增</button>
        {refreshMsg && <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text-muted)' }}>{refreshMsg}</span>}
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>代码</th><th>名称</th><th>交易所</th><th>币种</th>
            <th>分类</th><th>成分股数</th><th>来源</th><th>状态</th><th>最后拉取</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map(r => (
            <tr key={r.index_code}>
              <td>{r.index_code}</td>
              <td>{r.index_name}</td>
              <td>{r.exchange}</td>
              <td>{r.currency}</td>
              <td>{r.category || '—'}</td>
              <td>{r.constituent_count || '—'}</td>
              <td style={{ fontSize: 11 }}>{r.source}</td>
              <td>{r.is_active ? <span style={{ color: 'var(--chart-up)' }}>启用</span> : <span style={{ color: 'var(--text-muted)' }}>下架</span>}</td>
              <td style={{ fontSize: 11 }}>{r.last_pulled_at ? r.last_pulled_at.slice(0, 10) : '—'}</td>
              <td><button className="btn-ghost" onClick={() => setEditing(r)}>编辑</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
        共 {total} 条
        {page > 1 && <button className="btn-ghost" onClick={() => setPage(page - 1)} style={{ marginLeft: 8 }}>上一页</button>}
        {items.length >= PAGE_SIZE && <button className="btn-ghost" onClick={() => setPage(page + 1)} style={{ marginLeft: 8 }}>下一页</button>}
      </div>

      {editing && <IndexEditDialog row={editing} onClose={() => setEditing(null)} onSave={handleSave} />}
    </div>
  )
}

function IndexEditDialog({ row, onClose, onSave }) {
  const [data, setData] = useState({
    index_code: row.index_code || '',
    index_name: row.index_name || '',
    exchange: row.exchange || '',
    currency: row.currency || 'CNY',
    category: row.category || '',
    source: row.source || 'manual',
    is_active: row.is_active !== false,
  })

  return (
    <div className="modal-overlay">
      <div className="modal-box">
        <h3>{row.index_code ? '编辑指数' : '新增指数'}</h3>
        <label>代码 <input className="ig" value={data.index_code}
                            onChange={(e) => setData({...data, index_code: e.target.value})}
                            disabled={!!row.index_code} /></label>
        <label>名称 <input className="ig" value={data.index_name}
                            onChange={(e) => setData({...data, index_name: e.target.value})} /></label>
        <label>交易所 <input className="ig" value={data.exchange}
                              onChange={(e) => setData({...data, exchange: e.target.value})} /></label>
        <label>币种
          <select className="ig" value={data.currency}
                  onChange={(e) => setData({...data, currency: e.target.value})}>
            <option>CNY</option><option>USD</option><option>HKD</option>
          </select>
        </label>
        <label>分类
          <select className="ig" value={data.category}
                  onChange={(e) => setData({...data, category: e.target.value})}>
            <option value="">未分类</option>
            {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label>来源
          <select className="ig" value={data.source}
                  onChange={(e) => setData({...data, source: e.target.value})}>
            {SOURCES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label>启用 <input type="checkbox" checked={data.is_active}
                              onChange={(e) => setData({...data, is_active: e.target.checked})} /></label>
        <div style={{ marginTop: 12, textAlign: 'right' }}>
          <button className="btn-ghost" onClick={onClose}>取消</button>
          <button className="btn-ghost" style={{ marginLeft: 8 }}
                  onClick={() => onSave(data)}>保存</button>
        </div>
      </div>
    </div>
  )
}
