import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { rawApi as api } from '../api'

/**
 * 基金-指数映射 tab — 基于当前用户持仓 + SecurityMaster 的视图（2026-06-28 重构）。
 *
 * 数据源：/admin/fund-index-view（Holding LEFT JOIN SecurityMaster，过滤非股票）
 * 编辑：PUT /admin/security-master/{code} 更新 index_code/index_name/benchmark_formula/is_drillable
 * 无映射时显示 "-"，点击编辑可填入指数代码。
 *
 * 复用现有 .data-table / .btn-ghost / .ig / .raised / .subtab 样式。
 */
export default function FundIndexMapTab() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState(null)
  const [error, setError] = useState('')

  /** 拉取持仓基金 + SecurityMaster 映射视图。 */
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.get('/admin/fund-index-view')
      setItems(res.data.items || [])
    } catch (e) {
      console.error('加载基金-指数映射视图失败', e)
      setError(e.response?.data?.detail || e.message)
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  /** 保存编辑。 */
  const handleSave = async (data) => {
    setError('')
    try {
      await api.put(`/admin/security-master/${encodeURIComponent(data.security_code)}`, {
        is_drillable: data.is_drillable,
        index_code: data.index_code || null,
        index_name: data.index_name || null,
        benchmark_formula: data.benchmark_formula || null,
      })
      setEditing(null)
      load()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  /** 前端过滤（数据量 < 50，无需后端搜索）。 */
  const filtered = useMemo(() => {
    if (!search.trim()) return items
    const q = search.trim().toLowerCase()
    return items.filter(it =>
      (it.security_code || '').toLowerCase().includes(q) ||
      (it.security_name || '').toLowerCase().includes(q) ||
      (it.index_code || '').toLowerCase().includes(q) ||
      (it.index_name || '').toLowerCase().includes(q)
    )
  }, [items, search])

  return (
    <div>
      {/* 搜索栏 */}
      <div className="raised" style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <input
          className="ig"
          placeholder="搜索基金代码/名称/指数"
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ width: 240 }}
        />
        <button className="btn-ghost" onClick={load}>刷新</button>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
          共 {items.length} 只基金，{items.filter(it => it.is_drillable).length} 只可下钻
        </span>
      </div>

      {error && <div style={{ color: 'red', marginBottom: 8 }}>{error}</div>}

      {/* 表格 */}
      <table className="data-table">
        <thead>
          <tr>
            <th>基金代码</th>
            <th>基金名称</th>
            <th>类型</th>
            <th>市场</th>
            <th>可下钻</th>
            <th>指数代码</th>
            <th>指数名称</th>
            <th>业绩基准</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {filtered.length === 0 && (
            <tr><td colSpan="9" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              {loading ? '加载中...' : '暂无数据'}
            </td></tr>
          )}
          {filtered.map((row, i) => (
            <tr key={`${row.security_code}-${i}`}>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{row.security_code}</td>
              <td>{row.security_name || '-'}</td>
              <td>{row.security_type || '-'}</td>
              <td>{row.market || '-'}</td>
              <td style={{ textAlign: 'center' }}>
                {row.is_drillable
                  ? <span style={{ color: 'var(--accent, #6366f1)' }}>✓</span>
                  : <span style={{ color: 'var(--text-muted)' }}>—</span>}
              </td>
              <td style={{ fontFamily: 'GeistMono, monospace' }}>{row.index_code || '-'}</td>
              <td>{row.index_name || '-'}</td>
              <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {row.benchmark_formula || '-'}
              </td>
              <td>
                <button
                  className="btn-ghost"
                  style={{ padding: '2px 10px', fontSize: 11 }}
                  onClick={() => setEditing(row)}
                >
                  编辑
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* 编辑表单 */}
      {editing && (
        <FundIndexEditForm
          row={editing}
          onSave={handleSave}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  )
}

/**
 * 基金-指数映射编辑表单（居中模态框）。
 * 调用 PUT /admin/security-master/{code} 更新 4 个字段。
 */
function FundIndexEditForm({ row, onSave, onCancel }) {
  const [form, setForm] = useState({
    security_code: row.security_code,
    security_name: row.security_name || '',
    is_drillable: !!row.is_drillable,
    index_code: row.index_code || '',
    index_name: row.index_name || '',
    benchmark_formula: row.benchmark_formula || '',
  })

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
        <h3 style={{ marginTop: 0, marginBottom: 16 }}>编辑基金-指数映射</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            基金代码
            <div style={{ color: 'var(--text)', fontFamily: 'GeistMono, monospace' }}>{form.security_code}</div>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            基金名称
            <div style={{ color: 'var(--text)' }}>{form.security_name || '-'}</div>
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-muted)' }}>
            <input
              type="checkbox"
              checked={form.is_drillable}
              onChange={e => setField('is_drillable', e.target.checked)}
            />
            可下钻（is_drillable）
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            指数代码（留空清除映射）
            <input
              className="ig"
              value={form.index_code}
              onChange={e => setField('index_code', e.target.value)}
              placeholder="如 000300"
            />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            指数名称
            <input
              className="ig"
              value={form.index_name}
              onChange={e => setField('index_name', e.target.value)}
              placeholder="如 沪深300"
            />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            业绩基准
            <input
              className="ig"
              value={form.benchmark_formula}
              onChange={e => setField('benchmark_formula', e.target.value)}
              placeholder="如 沪深300指数×95%+银行活期×5%"
            />
          </label>
        </div>
        <div style={{ marginTop: 20, display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn-ghost" onClick={onCancel}>取消</button>
          <button
            className="btn-ghost"
            style={{ background: 'var(--accent, #6366f1)', color: '#fff', border: 'none', padding: '6px 16px' }}
            onClick={() => onSave(form)}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
