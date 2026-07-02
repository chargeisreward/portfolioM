import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

const DIMENSIONS = [
  { code: 'asset_type', label: '资产类型' },
  { code: 'theme',      label: '主题' },
]
const DIM_LABELS = Object.fromEntries(DIMENSIONS.map(d => [d.code, d.label]))

/**
 * 分类维度管理 tab — 两个 dimension 切换;字典 CRUD + 停用。
 */
export default function ClassificationTab() {
  const [dimension, setDimension] = useState('asset_type')
  const [items, setItems] = useState([])
  const [editing, setEditing] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get('/admin/classification', { params: { dimension } })
      setItems(res.data || [])
    } catch (e) {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [dimension])

  useEffect(() => { load() }, [load])

  const handleSave = async (data) => {
    try {
      if (editing.id) {
        await api.put(`/admin/classification/${editing.id}`, data)
      } else {
        await api.post('/admin/classification', { ...data, dimension })
      }
      setEditing(null)
      load()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  const handleDeactivate = async (row) => {
    if (!confirm(`停用「${row.display_label}」吗?`)) return
    try {
      await api.delete(`/admin/classification/${row.id}`)
      load()
    } catch (e) {
      alert('停用失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  return (
    <div className="raised" style={{ padding: 12 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>维度:</span>
        {DIMENSIONS.map(d => (
          <button key={d.code}
                  className={dimension === d.code ? 'btn-ghost on' : 'btn-ghost'}
                  onClick={() => setDimension(d.code)}>{d.label}</button>
        ))}
        <button className="btn-ghost" style={{ marginLeft: 'auto' }}
                onClick={() => setEditing({})}>+ 新增</button>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>维度</th><th>code</th><th>显示标签</th><th>排序</th><th>启用</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map(r => (
            <tr key={r.id}>
              <td>{DIM_LABELS[r.dimension] || r.dimension}</td>
              <td style={{ fontFamily: 'monospace' }}>{r.code}</td>
              <td>{r.display_label}</td>
              <td>{r.sort_order}</td>
              <td>{r.is_active ? '✓' : '停'}</td>
              <td>
                <button className="btn-ghost" onClick={() => setEditing(r)}>编辑</button>
                {r.is_active && <button className="btn-ghost" style={{ marginLeft: 4 }} onClick={() => handleDeactivate(r)}>停用</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing && <ClassifyEditDialog row={editing} dimension={dimension}
                                       onClose={() => setEditing(null)}
                                       onSave={handleSave} />}
    </div>
  )
}

function ClassifyEditDialog({ row, dimension, onClose, onSave }) {
  const [data, setData] = useState({
    code: row.code || '',
    display_label: row.display_label || '',
    sort_order: row.sort_order || 0,
    is_active: row.is_active !== false,
  })

  return (
    <div className="modal-overlay">
      <div className="modal-box">
        <h3>{row.id ? '编辑' : '新增'}「{DIM_LABELS[dimension] || dimension}」分类</h3>
        <label>code <input className="ig" value={data.code}
                            onChange={(e) => setData({...data, code: e.target.value})}
                            disabled={!!row.id} /></label>
        <label>显示标签 <input className="ig" value={data.display_label}
                                 onChange={(e) => setData({...data, display_label: e.target.value})} /></label>
        <label>排序 <input className="ig" type="number" value={data.sort_order}
                            onChange={(e) => setData({...data, sort_order: parseInt(e.target.value) || 0})} /></label>
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
