import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

/**
 * 证券主数据 tab — 分页表格 + 筛选 + CRUD + 同步。
 * 复用现有 .data-table / .btn-ghost / .ig / .raised 样式。
 */
export default function SecurityMasterTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ type: '', market: '', drillable: '', search: '' })
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null) // null | row dict
  const [showAdd, setShowAdd] = useState(false)

  const PAGE_SIZE = 50

  /** 拉取证券主数据列表。 */
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: PAGE_SIZE }
      if (filters.type) params.type = filters.type
      if (filters.market) params.market = filters.market
      if (filters.drillable !== '') params.drillable = filters.drillable
      if (filters.search) params.search = filters.search
      const res = await api.get('/admin/security-master', { params })
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch (e) {
      console.error('加载证券主数据失败', e)
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [page, filters])

  useEffect(() => { load() }, [load])

  /** 切换可下钻状态。 */
  const handleToggleDrillable = async (row) => {
    try {
      await api.put(`/admin/security-master/${encodeURIComponent(row.security_code)}`, {
        is_drillable: !row.is_drillable,
      })
      load()
    } catch (e) {
      alert('更新失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  /** 触发同步/初始化操作。 */
  const handleSync = async (action) => {
    const endpointMap = {
      holdings: '/admin/security-master/sync-from-holdings',
      drill: '/admin/security-master/sync-from-drill',
      init: '/admin/security-master/init',
    }
    const labelMap = { holdings: '同步持仓', drill: '同步下钻', init: '初始化' }
    try {
      const res = await api.post(endpointMap[action])
      const count = res.data.synced ?? res.data.initialized ?? 0
      alert(`${labelMap[action]}完成：${count} 条`)
      load()
    } catch (e) {
      alert(`${labelMap[action]}失败: ` + (e.response?.data?.detail || e.message))
    }
  }

  /** 保存（新增或编辑）。 */
  const handleSave = async (data) => {
    try {
      if (editing) {
        await api.put(`/admin/security-master/${encodeURIComponent(editing.security_code)}`, data)
      } else {
        await api.post('/admin/security-master', data)
      }
      setEditing(null)
      setShowAdd(false)
      load()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  /** 删除证券。 */
  const handleDelete = async (code) => {
    if (!confirm(`确认删除 ${code}？`)) return
    try {
      await api.delete(`/admin/security-master/${encodeURIComponent(code)}`)
      load()
    } catch (e) {
      alert('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div>
      {/* 筛选 + 操作栏 */}
      <div className="raised" style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="ig" value={filters.type} onChange={e => { setFilters({ ...filters, type: e.target.value }); setPage(1) }}>
          <option value="">全部类型</option>
          <option value="fund">基金</option>
          <option value="stock">股票</option>
          <option value="bond">债券</option>
        </select>
        <select className="ig" value={filters.market} onChange={e => { setFilters({ ...filters, market: e.target.value }); setPage(1) }}>
          <option value="">全部市场</option>
          <option value="CN">CN</option>
          <option value="HK">HK</option>
          <option value="US">US</option>
          <option value="OF">OF</option>
        </select>
        <select className="ig" value={filters.drillable} onChange={e => { setFilters({ ...filters, drillable: e.target.value }); setPage(1) }}>
          <option value="">全部</option>
          <option value="true">可下钻</option>
          <option value="false">不可下钻</option>
        </select>
        <input
          className="ig"
          placeholder="搜索代码/名称"
          value={filters.search}
          onChange={e => { setFilters({ ...filters, search: e.target.value }); setPage(1) }}
          style={{ width: 150 }}
        />
        <button className="btn-ghost" onClick={load}>查询</button>
        <button className="btn-ghost" onClick={() => setShowAdd(true)}>+ 新增</button>
        <button className="btn-ghost" onClick={() => handleSync('holdings')}>同步持仓</button>
        <button className="btn-ghost" onClick={() => handleSync('drill')}>同步下钻</button>
        <button className="btn-ghost" onClick={() => handleSync('init')}>初始化</button>
      </div>

      {/* 表格 */}
      <table className="data-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>类型</th>
            <th>市场</th>
            <th>基金类型</th>
            <th>可下钻</th>
            <th>跟踪指数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr><td colSpan="8" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              {loading ? '加载中...' : '暂无数据'}
            </td></tr>
          )}
          {items.map(row => (
            <tr key={row.security_code}>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{row.security_code}</td>
              <td>{row.security_name || '-'}</td>
              <td>{row.security_type || '-'}</td>
              <td>{row.market || '-'}</td>
              <td>{row.fund_type || '-'}</td>
              <td>
                <input
                  type="checkbox"
                  checked={row.is_drillable || false}
                  onChange={() => handleToggleDrillable(row)}
                />
              </td>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{row.index_code || '-'}</td>
              <td>
                <button className="btn-ghost" style={{ padding: '2px 10px', fontSize: 11 }} onClick={() => setEditing(row)}>编辑</button>
                <button className="btn-ghost" style={{ padding: '2px 10px', fontSize: 11, marginLeft: 4 }} onClick={() => handleDelete(row.security_code)}>删除</button>
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
        <SecurityEditForm
          row={editing}
          onSave={handleSave}
          onCancel={() => { setEditing(null); setShowAdd(false) }}
        />
      )}
    </div>
  )
}

/**
 * 证券编辑/新增表单（居中模态框）。
 */
function SecurityEditForm({ row, onSave, onCancel }) {
  const [form, setForm] = useState(row ? { ...row } : {
    security_code: '', security_name: '', security_type: 'fund',
    asset_type: '', market: 'CN', fund_type: '', is_drillable: false,
    index_code: '', index_name: '', benchmark_formula: '', note: '',
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
        <h3 style={{ marginTop: 0, marginBottom: 16 }}>{row ? '编辑证券' : '新增证券'}</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            代码
            {row
              ? <div style={{ color: 'var(--text)', fontFamily: 'GeistMono, monospace' }}>{form.security_code}</div>
              : <input className="ig" value={form.security_code} onChange={e => setField('security_code', e.target.value)} />
            }
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            名称
            <input className="ig" value={form.security_name || ''} onChange={e => setField('security_name', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            类型
            <select className="ig" value={form.security_type || 'fund'} onChange={e => setField('security_type', e.target.value)}>
              <option value="fund">基金</option>
              <option value="stock">股票</option>
              <option value="bond">债券</option>
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            asset_type
            <input className="ig" value={form.asset_type || ''} onChange={e => setField('asset_type', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            市场
            <select className="ig" value={form.market || 'CN'} onChange={e => setField('market', e.target.value)}>
              <option value="CN">CN</option>
              <option value="HK">HK</option>
              <option value="US">US</option>
              <option value="OF">OF</option>
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            基金类型
            <select className="ig" value={form.fund_type || ''} onChange={e => setField('fund_type', e.target.value)}>
              <option value="">-</option>
              <option value="etf">ETF(场内)</option>
              <option value="otc">OTC(场外)</option>
            </select>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-muted)' }}>
            <input type="checkbox" checked={form.is_drillable || false} onChange={e => setField('is_drillable', e.target.checked)} />
            可下钻
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            跟踪指数代码
            <input className="ig" value={form.index_code || ''} onChange={e => setField('index_code', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            跟踪指数名称
            <input className="ig" value={form.index_name || ''} onChange={e => setField('index_name', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            业绩基准
            <input className="ig" value={form.benchmark_formula || ''} onChange={e => setField('benchmark_formula', e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            备注
            <input className="ig" value={form.note || ''} onChange={e => setField('note', e.target.value)} />
          </label>
        </div>
        <div style={{ marginTop: 20, display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn-ghost" onClick={onCancel}>取消</button>
          <button className="btn-ghost" style={{ background: 'var(--accent, #6366f1)', color: '#fff', border: 'none', padding: '6px 16px' }} onClick={() => onSave(form)}>保存</button>
        </div>
      </div>
    </div>
  )
}
