import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

const ASSET_TYPES = [
  'a_share_equity', 'hk_equity', 'us_stock',
  'bond', 'gold', 'commodity',
]
const ASSET_TYPE_LABELS = {
  a_share_equity: 'A 股股票',
  hk_equity: '港股',
  us_stock: '美股',
  bond: '债券',
  gold: '黄金',
  commodity: '商品',
}

/**
 * 股票主数据 tab — 分页表格 + 筛选 + CRUD。
 */
export default function StockMasterTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ asset_type: '', search: '' })
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null)
  const PAGE_SIZE = 50

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: PAGE_SIZE }
      if (filters.asset_type) params.asset_type = filters.asset_type
      if (filters.search) params.search = filters.search
      const res = await api.get('/admin/stock-master', { params })
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
      if (editing.stock_code) {
        await api.put(`/admin/stock-master/${encodeURIComponent(editing.stock_code)}`, data)
      } else {
        await api.post('/admin/stock-master', data)
      }
      setEditing(null)
      load()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  return (
    <div className="raised" style={{ padding: 12 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <input className="ig" placeholder="搜索代码/名称"
               value={filters.search}
               onChange={(e) => setFilters({...filters, search: e.target.value, page: 1})} />
        <select className="ig" value={filters.asset_type}
                onChange={(e) => setFilters({...filters, asset_type: e.target.value, page: 1})}>
          <option value="">全部类型</option>
          {ASSET_TYPES.map(t => <option key={t} value={t}>{ASSET_TYPE_LABELS[t] || t}</option>)}
        </select>
        <button className="btn-ghost" onClick={() => setEditing({})}>+ 新增</button>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>代码</th><th>名称</th><th>交易所</th><th>币种</th>
            <th>资产类型</th><th>可下钻</th><th>备注</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map(r => (
            <tr key={r.stock_code}>
              <td>{r.stock_code}</td>
              <td>{r.stock_name}</td>
              <td>{r.exchange}</td>
              <td>{r.currency}</td>
              <td>{ASSET_TYPE_LABELS[r.asset_type] || r.asset_type}</td>
              <td>{r.is_drillable ? '✓' : '—'}</td>
              <td>{r.note}</td>
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

      {editing && <StockEditDialog row={editing} onClose={() => setEditing(null)} onSave={handleSave} />}
    </div>
  )
}

function StockEditDialog({ row, onClose, onSave }) {
  const [data, setData] = useState({
    stock_code: row.stock_code || '',
    stock_name: row.stock_name || '',
    exchange: row.exchange || '',
    currency: row.currency || 'CNY',
    asset_type: row.asset_type || 'a_share_equity',
    note: row.note || '',
  })

  return (
    <div className="modal-overlay">
      <div className="modal-box">
        <h3>{row.stock_code ? '编辑股票' : '新增股票'}</h3>
        <label>代码 <input className="ig" value={data.stock_code}
                            onChange={(e) => setData({...data, stock_code: e.target.value})}
                            disabled={!!row.stock_code} /></label>
        <label>名称 <input className="ig" value={data.stock_name}
                            onChange={(e) => setData({...data, stock_name: e.target.value})} /></label>
        <label>交易所 <input className="ig" value={data.exchange}
                              onChange={(e) => setData({...data, exchange: e.target.value})} /></label>
        <label>币种
          <select className="ig" value={data.currency}
                  onChange={(e) => setData({...data, currency: e.target.value})}>
            <option>CNY</option><option>USD</option><option>HKD</option><option>CAD</option>
          </select>
        </label>
        <label>资产类型
          <select className="ig" value={data.asset_type}
                  onChange={(e) => setData({...data, asset_type: e.target.value})}>
            {ASSET_TYPES.map(t => <option key={t} value={t}>{ASSET_TYPE_LABELS[t] || t}</option>)}
          </select>
        </label>
        <label>备注 <input className="ig" value={data.note}
                            onChange={(e) => setData({...data, note: e.target.value})} /></label>
        <div style={{ marginTop: 12, textAlign: 'right' }}>
          <button className="btn-ghost" onClick={onClose}>取消</button>
          <button className="btn-ghost" style={{ marginLeft: 8 }}
                  onClick={() => onSave(data)}>保存</button>
        </div>
      </div>
    </div>
  )
}
