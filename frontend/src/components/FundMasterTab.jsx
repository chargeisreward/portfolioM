import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

const FUND_ASSET_TYPES = [
  'a_share_etf', 'qdii_equity', 'qdii_bond', 'gold', 'commodity', 'a_share_equity',
]
const FUND_ASSET_LABELS = {
  a_share_etf: 'A 股 ETF',
  qdii_equity: 'QDII 基金',
  qdii_bond: 'QDII 债基',
  gold: '黄金 ETF',
  commodity: '商品 ETF',
  a_share_equity: 'A 股联接基金',
}

/**
 * 基金主数据 tab — 分页表格 + 筛选 + CRUD。
 */
export default function FundMasterTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ asset_type: '', fund_type: '', search: '' })
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null)
  const PAGE_SIZE = 50

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: PAGE_SIZE }
      if (filters.asset_type) params.asset_type = filters.asset_type
      if (filters.fund_type) params.fund_type = filters.fund_type
      if (filters.search) params.search = filters.search
      const res = await api.get('/admin/fund-master', { params })
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
      if (editing.fund_code) {
        await api.put(`/admin/fund-master/${encodeURIComponent(editing.fund_code)}`, data)
      } else {
        await api.post('/admin/fund-master', data)
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
        <select className="ig" value={filters.fund_type}
                onChange={(e) => setFilters({...filters, fund_type: e.target.value, page: 1})}>
          <option value="">全部场内场外</option>
          <option value="etf">场内</option>
          <option value="otc">场外</option>
        </select>
        <select className="ig" value={filters.asset_type}
                onChange={(e) => setFilters({...filters, asset_type: e.target.value, page: 1})}>
          <option value="">全部类型</option>
          {FUND_ASSET_TYPES.map(t => <option key={t} value={t}>{FUND_ASSET_LABELS[t] || t}</option>)}
        </select>
        <button className="btn-ghost" onClick={() => setEditing({})}>+ 新增</button>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>代码</th><th>名称</th><th>场内/场外</th><th>类型</th>
            <th>业绩基准</th><th>可下钻</th><th>备注</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map(r => (
            <tr key={r.fund_code}>
              <td>{r.fund_code}</td>
              <td>{r.fund_name}</td>
              <td>{r.fund_type === 'etf' ? '场内' : '场外'}</td>
              <td>{FUND_ASSET_LABELS[r.asset_type] || r.asset_type}</td>
              <td style={{ fontSize: 11 }}>{r.benchmark_formula}</td>
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

      {editing && <FundEditDialog row={editing} onClose={() => setEditing(null)} onSave={handleSave} />}
    </div>
  )
}

function FundEditDialog({ row, onClose, onSave }) {
  const [data, setData] = useState({
    fund_code: row.fund_code || '',
    fund_name: row.fund_name || '',
    fund_type: row.fund_type || 'etf',
    asset_type: row.asset_type || 'a_share_etf',
    benchmark_formula: row.benchmark_formula || '',
    note: row.note || '',
  })

  return (
    <div className="modal-overlay">
      <div className="modal-box">
        <h3>{row.fund_code ? '编辑基金' : '新增基金'}</h3>
        <label>代码 <input className="ig" value={data.fund_code}
                            onChange={(e) => setData({...data, fund_code: e.target.value})}
                            disabled={!!row.fund_code} /></label>
        <label>名称 <input className="ig" value={data.fund_name}
                            onChange={(e) => setData({...data, fund_name: e.target.value})} /></label>
        <label>场内/场外
          <select className="ig" value={data.fund_type}
                  onChange={(e) => setData({...data, fund_type: e.target.value})}>
            <option value="etf">场内</option>
            <option value="otc">场外</option>
          </select>
        </label>
        <label>类型
          <select className="ig" value={data.asset_type}
                  onChange={(e) => setData({...data, asset_type: e.target.value})}>
            {FUND_ASSET_TYPES.map(t => <option key={t} value={t}>{FUND_ASSET_LABELS[t] || t}</option>)}
          </select>
        </label>
        <label>业绩基准 <input className="ig" style={{width:'100%'}}
                                   value={data.benchmark_formula}
                                   onChange={(e) => setData({...data, benchmark_formula: e.target.value})} /></label>
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
