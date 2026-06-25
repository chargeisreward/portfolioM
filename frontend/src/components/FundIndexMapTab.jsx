import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

/**
 * 基金-指数映射 tab — 分页列表 + 搜索 + CRUD。
 * 复用现有 .data-table / .btn-ghost / .ig / .raised 样式。
 */
export default function FundIndexMapTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null)
  const [showAdd, setShowAdd] = useState(false)

  const PAGE_SIZE = 50

  /** 拉取基金-指数映射列表。 */
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/admin/fund-index-map', { params: { search, page, page_size: PAGE_SIZE } })
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch (e) {
      console.error('加载基金-指数映射失败', e)
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [search, page])

  useEffect(() => { load() }, [load])

  /** 保存（新增或编辑）。 */
  const handleSave = async (data) => {
    try {
      if (editing) {
        await api.put(`/admin/fund-index-map/${encodeURIComponent(editing.fund_code)}/${editing.as_of_date}`, data)
      } else {
        await api.post('/admin/fund-index-map', data)
      }
      setEditing(null)
      setShowAdd(false)
      load()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  /** 删除映射。 */
  const handleDelete = async (fundCode, asOfDate) => {
    if (!confirm(`确认删除 ${fundCode}？`)) return
    try {
      await api.delete(`/admin/fund-index-map/${encodeURIComponent(fundCode)}/${asOfDate}`)
      load()
    } catch (e) {
      alert('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      {/* 搜索 + 操作栏 */}
      <div className="raised" style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <input
          className="ig"
          placeholder="搜索基金代码/名称/指数"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1) }}
          style={{ width: 220 }}
        />
        <button className="btn-ghost" onClick={load}>查询</button>
        <button className="btn-ghost" onClick={() => setShowAdd(true)}>+ 新增</button>
      </div>

      {/* 表格 */}
      <table className="data-table">
        <thead>
          <tr>
            <th>基金代码</th>
            <th>基金名称</th>
            <th>指数代码</th>
            <th>指数名称</th>
            <th>业绩基准</th>
            <th>日期</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr><td colSpan="7" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              {loading ? '加载中...' : '暂无数据'}
            </td></tr>
          )}
          {items.map((row, i) => (
            <tr key={`${row.fund_code}-${row.as_of_date}-${i}`}>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{row.fund_code}</td>
              <td>{row.fund_name || '-'}</td>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{row.index_code || '-'}</td>
              <td>{row.index_name || '-'}</td>
              <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {row.benchmark_formula || '-'}
              </td>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{row.as_of_date}</td>
              <td>
                <button className="btn-ghost" style={{ padding: '2px 10px', fontSize: 11 }} onClick={() => setEditing(row)}>编辑</button>
                <button className="btn-ghost" style={{ padding: '2px 10px', fontSize: 11, marginLeft: 4 }} onClick={() => handleDelete(row.fund_code, row.as_of_date)}>删除</button>
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

      {/* 编辑/新增表单 */}
      {(editing || showAdd) && (
        <FundIndexEditForm
          row={editing}
          onSave={handleSave}
          onCancel={() => { setEditing(null); setShowAdd(false) }}
        />
      )}
    </div>
  )
}

/**
 * 基金-指数映射编辑表单（居中模态框）。
 */
function FundIndexEditForm({ row, onSave, onCancel }) {
  const [form, setForm] = useState(row ? { ...row } : {
    fund_code: '', fund_name: '', index_code: '', index_name: '',
    benchmark_formula: '', as_of_date: new Date().toISOString().slice(0, 10), source: 'manual',
  })

  /** 更新表单字段。 */
  const setField = (key, val) => setForm(prev => ({ ...prev, [key]: val }))

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--bg, #fff)', padding: 24, borderRadius: 4,
        minWidth: 420, maxWidth: 480, maxHeight: '90vh', overflowY: 'auto',
        border: '1px solid var(--border)',
      }}>
        <h3 style={{ marginTop: 0, marginBottom: 16 }}>{row ? '编辑映射' : '新增映射'}</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            基金代码
            {row
              ? <div style={{ color: 'var(--text)', fontFamily: 'GeistMono, monospace' }}>{form.fund_code}</div>
              : <input className="ig" value={form.fund_code} onChange={e => setField('fund_code', e.target.value)} />
            }
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            基金名称
            <input className="ig" value={form.fund_name || ''} onChange={e => setField('fund_name', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            指数代码
            <input className="ig" value={form.index_code || ''} onChange={e => setField('index_code', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            指数名称
            <input className="ig" value={form.index_name || ''} onChange={e => setField('index_name', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            业绩基准
            <input className="ig" value={form.benchmark_formula || ''} onChange={e => setField('benchmark_formula', e.target.value)} />
          </label>
          {!row && (
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
              日期
              <input className="ig" type="date" value={form.as_of_date} onChange={e => setField('as_of_date', e.target.value)} />
            </label>
          )}
        </div>
        <div style={{ marginTop: 20, display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn-ghost" onClick={onCancel}>取消</button>
          <button className="btn-ghost" style={{ background: 'var(--accent, #6366f1)', color: '#fff', border: 'none', padding: '6px 16px' }} onClick={() => onSave(form)}>保存</button>
        </div>
      </div>
    </div>
  )
}
