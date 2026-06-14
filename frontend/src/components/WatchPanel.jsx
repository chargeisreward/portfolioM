import React, { useState, useRef, useEffect } from 'react'
import * as echarts from 'echarts'
import * as api from '../api'

export default function WatchPanel() {
  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(false)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState(null)
  const indRef = useRef(null)
  const regRef = useRef(null)

  // 加载关注列表
  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getWatchlist()
      setList(data || [])
    } catch (e) {
      setError('加载关注清单失败：' + (e?.message || e))
    }
    setLoading(false)
  }
  useEffect(() => { reload() }, [])

  // 防抖搜索
  useEffect(() => {
    if (!q.trim()) { setResults([]); return }
    const timer = setTimeout(async () => {
      setSearching(true)
      try {
        const data = await api.searchSecurities(q.trim())
        setResults(data || [])
      } catch (e) {
        setResults([])
      }
      setSearching(false)
    }, 300)
    return () => clearTimeout(timer)
  }, [q])

  // 饼图
  useEffect(() => {
    if (indRef.current) {
      const c = echarts.init(indRef.current)
      const byInd = {}
      list.forEach(w => { byInd[w.industry || '未分类'] = (byInd[w.industry || '未分类'] || 0) + w.weight })
      c.setOption({
        tooltip: { trigger: 'item' },
        series: [{
          type: 'pie', radius: ['40%', '65%'],
          data: Object.entries(byInd).map(([k, v]) => ({ name: k, value: v })),
          label: { color: '#9ca3af' },
        }],
      })
    }
    if (regRef.current) {
      const c = echarts.init(regRef.current)
      const byReg = {}
      list.forEach(w => { byReg[w.market || '其他'] = (byReg[w.market || '其他'] || 0) + w.weight })
      c.setOption({
        tooltip: { trigger: 'item' },
        series: [{
          type: 'pie', radius: ['40%', '65%'],
          data: Object.entries(byReg).map(([k, v]) => ({ name: k, value: v })),
          label: { color: '#9ca3af' },
        }],
      })
    }
  }, [list])

  const add = async (r) => {
    if (list.find(w => w.code === r.code)) return
    try {
      const res = await api.addWatchlist(r.code)
      if (res?.status === 'ok') {
        setList([res.row, ...list])
        setQ(''); setResults([])
      } else {
        setError(res?.message || '添加失败')
      }
    } catch (e) {
      setError('添加失败：' + (e?.message || e))
    }
  }

  const remove = async (code) => {
    try {
      await api.removeWatchlist(code)
      setList(list.filter(w => w.code !== code))
    } catch (e) {
      setError('移除失败：' + (e?.message || e))
    }
  }

  const updateWeight = async (code, weight) => {
    setList(list.map(w => w.code === code ? { ...w, weight } : w))
    try { await api.setWatchlistWeight(code, weight) } catch (e) { /* 后台再拉 */ }
  }

  // KPI
  const totalWeight = list.reduce((s, w) => s + (w.weight || 0), 0)
  const peValues = list.map(w => parseFloat(w.pe_ttm)).filter(v => !isNaN(v) && v > 0)
  const avgPE = peValues.length ? (peValues.reduce((s, v) => s + v, 0) / peValues.length).toFixed(1) : '-'
  // 加权市值（仅 CNY 折算后）
  const totalMktCapCNY = list.reduce((s, w) => {
    if (!w.price || !w.weight) return s
    return s + w.price * 100 * w.weight  // 占位（无真实股数）
  }, 0)

  return (
    <div>
      {error && (
        <div className="raised" style={{ borderColor: 'var(--down)', color: 'var(--down)' }}>
          {error}
        </div>
      )}

      <div className="raised">
        <div className="section-title">添加关注</div>
        <input
          className="ig" style={{ width: '100%' }}
          placeholder="输入代码或名称（159316 / 589720 / NVDA / 00700）"
          value={q} onChange={e => setQ(e.target.value)}
        />
        {searching && <div style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: 12 }}>搜索中…</div>}
        {results.length > 0 && (
          <div style={{ marginTop: 8 }}>
            {results.map(r => (
              <div key={r.code} style={{ display: 'flex', alignItems: 'center', padding: '8px 12px', borderBottom: '1px solid rgba(255,255,255,0.04)', gap: 8 }}>
                <span style={{ flex: 1 }}>
                  {r.name} ({r.code}) — {r.market} | {r.industry} | {r.price != null ? r.price : '—'}
                </span>
                <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => add(r)}>+ 添加</button>
              </div>
            ))}
          </div>
        )}
        {q.trim() && !searching && results.length === 0 && (
          <div style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: 12 }}>无匹配证券</div>
        )}
      </div>

      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-label">关注数量</div>
          <div className="kpi-value">{list.length}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">总权重</div>
          <div className="kpi-value">{totalWeight.toFixed(0)}%</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">平均PE</div>
          <div className="kpi-value">{avgPE}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">有价标的</div>
          <div className="kpi-value">{list.filter(w => w.price != null).length}</div>
        </div>
      </div>

      {list.length > 0 && (
        <div className="chart-grid" style={{ marginBottom: 16 }}>
          <div className="raised"><div className="section-title">行业分布</div><div ref={indRef} style={{ width: '100%', height: 250 }} /></div>
          <div className="raised"><div className="section-title">区域分布</div><div ref={regRef} style={{ width: '100%', height: 250 }} /></div>
        </div>
      )}

      <div className="raised" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px 8px' }}>
          <div className="section-title" style={{ marginBottom: 0 }}>关注列表 · {list.length}项</div>
        </div>
        <div className="table-wrap" style={{ maxHeight: 500, overflowY: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>代码</th><th>名称</th><th>市场</th><th>行业</th>
                <th style={{ textAlign: 'right' }}>权重%</th>
                <th style={{ textAlign: 'right' }}>现价</th>
                <th style={{ textAlign: 'right' }}>涨跌%</th>
                <th style={{ textAlign: 'right' }}>PE</th>
                <th>市值</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={10} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 30 }}>加载中…</td></tr>
              )}
              {!loading && list.length === 0 && (
                <tr><td colSpan={10} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 30 }}>还没有关注任何证券</td></tr>
              )}
              {!loading && list.map(w => (
                <tr key={w.code}>
                  <td>{w.code}</td>
                  <td>{w.name || '-'}</td>
                  <td style={{ color: 'var(--text-secondary)' }}>{w.market || '-'}</td>
                  <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{w.industry || '-'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <input type="number" min="0" max="100" step="0.5"
                      value={w.weight}
                      onChange={e => updateWeight(w.code, parseFloat(e.target.value) || 0)}
                      style={{ width: 60, textAlign: 'right', background: 'transparent', border: '1px solid var(--border)', color: 'var(--text)', padding: '2px 4px', fontFamily: '"GeistMono", monospace', fontSize: 12 }}
                    />
                  </td>
                  <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace' }}>
                    {w.price != null ? w.price : '-'}
                  </td>
                  <td style={{ textAlign: 'right', color: (w.change_pct ?? 0) >= 0 ? 'var(--up)' : 'var(--down)' }}>
                    {w.change_pct != null ? `${w.change_pct >= 0 ? '+' : ''}${w.change_pct}%` : '-'}
                  </td>
                  <td style={{ textAlign: 'right' }}>{w.pe_ttm || '-'}</td>
                  <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{w.market_cap || '-'}</td>
                  <td>
                    <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => remove(w.code)}>移除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
