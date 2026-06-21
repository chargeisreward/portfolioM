import React, { useState, useEffect } from 'react'
import * as api from '../api'

/**
 * 数据浏览页面：双重标签页 + 分页
 * 外层标签页 = 数据分类（持仓/行情/分析/基础）
 * 内层标签页 = 具体数据表
 */
export default function DataBrowser() {
  const [tableMap, setTableMap] = useState({})
  const [categories, setCategories] = useState([])
  const [activeCat, setActiveCat] = useState('')
  const [activeTable, setActiveTable] = useState('')
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const pageSize = 50

  // 加载表结构
  useEffect(() => {
    api.getDataTables().then(map => {
      setTableMap(map)
      const cats = Object.keys(map)
      setCategories(cats)
      if (cats.length > 0) {
        setActiveCat(cats[0])
        const tables = map[cats[0]]
        if (tables.length > 0) setActiveTable(tables[0].table)
      }
    }).catch(() => {})
  }, [])

  // 切换分类时重置表选择
  useEffect(() => {
    if (activeCat && tableMap[activeCat]?.length > 0) {
      setActiveTable(tableMap[activeCat][0].table)
      setPage(1)
    }
  }, [activeCat])

  // 切换表时重置页码
  useEffect(() => {
    setPage(1)
  }, [activeTable])

  // 加载数据
  useEffect(() => {
    if (!activeTable) return
    setLoading(true)
    api.browseTable(activeTable, page, pageSize)
      .then(d => { setData(d); setLoading(false) })
      .catch(() => { setData(null); setLoading(false) })
  }, [activeTable, page])

  // 当前分类下的表列表
  const currentTables = tableMap[activeCat] || []
  // 当前表的标签
  const currentLabel = currentTables.find(t => t.table === activeTable)?.label || activeTable

  return (
    <div>
      <div className="raised" style={{ padding: 0, overflow: 'hidden' }}>
        {/* 外层标签页：分类 */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border)' }}>
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setActiveCat(cat)}
              style={{
                padding: '8px 16px',
                background: activeCat === cat ? 'var(--bg-raised)' : 'transparent',
                border: 'none',
                borderBottom: activeCat === cat ? '2px solid var(--text)' : '2px solid transparent',
                color: activeCat === cat ? 'var(--text)' : 'var(--text-muted)',
                cursor: 'pointer',
                fontSize: 11,
                fontFamily: '"GeistMono", monospace',
                letterSpacing: 0.5,
                textTransform: 'uppercase',
              }}
            >
              {cat}
            </button>
          ))}
        </div>

        {/* 内层标签页：具体表 */}
        <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border)', background: 'var(--bg)' }}>
          {currentTables.map(t => (
            <button
              key={t.table}
              onClick={() => setActiveTable(t.table)}
              className={activeTable === t.table ? 'cur-btn on' : 'cur-btn'}
              style={{ margin: '6px 2px', fontSize: 10 }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* 表格标题和分页 */}
        <div style={{ padding: '8px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>
            {currentLabel} · {data?.total ?? 0}条
          </span>
          {data && data.total_pages > 1 && (
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="cur-btn"
                style={{ fontSize: 9 }}
              >上一页</button>
              <span style={{ fontSize: 10, color: 'var(--text-secondary)', fontFamily: '"GeistMono", monospace' }}>
                {page}/{data.total_pages}
              </span>
              <button
                onClick={() => setPage(p => Math.min(data.total_pages, p + 1))}
                disabled={page >= data.total_pages}
                className="cur-btn"
                style={{ fontSize: 9 }}
              >下一页</button>
            </div>
          )}
        </div>

        {/* 数据表格 */}
        <div style={{ maxHeight: 600, overflow: 'auto' }}>
          {loading ? (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>加载中...</div>
          ) : data && data.rows && data.rows.length > 0 ? (
            <table className="data-table">
              <thead>
                <tr>
                  {data.columns.map(col => (
                    <th key={col}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i}>
                    {data.columns.map(col => (
                      <td key={col} style={{
                        maxWidth: 200,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        fontFamily: typeof row[col] === 'number' ? '"GeistMono", monospace' : 'inherit',
                      }} title={String(row[col] ?? '')}>
                        {row[col] == null ? '-' : String(row[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>暂无数据</div>
          )}
        </div>
      </div>
    </div>
  )
}
