import React, { useState, useEffect, useMemo } from 'react'
import * as api from '../api'

/**
 * 数据浏览页面：双重标签页 + 分页 + 行内编辑
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
  const [options, setOptions] = useState({ asset_type: [], type2: [] })
  const [editingCell, setEditingCell] = useState(null)  // {rowIdx, col}
  const [savingCell, setSavingCell] = useState(false)
  const [toast, setToast] = useState(null)
  const pageSize = 50

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

  useEffect(() => {
    api.getDataBrowserOptions().then(setOptions).catch(() => {})
  }, [])

  useEffect(() => {
    if (activeCat && tableMap[activeCat]?.length > 0) {
      setActiveTable(tableMap[activeCat][0].table)
      setPage(1)
    }
  }, [activeCat])

  useEffect(() => { setPage(1) }, [activeTable])

  useEffect(() => {
    if (!activeTable) return
    setLoading(true)
    setEditingCell(null)
    api.browseTable(activeTable, page, pageSize)
      .then(d => { setData(d); setLoading(false) })
      .catch(() => { setData(null); setLoading(false) })
  }, [activeTable, page])

  const currentTables = tableMap[activeCat] || []
  const currentLabel = currentTables.find(t => t.table === activeTable)?.label || activeTable
  const editableSet = useMemo(() => new Set(data?.editable_columns || []), [data])

  const showToast = (msg, type = 'ok') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 2500)
  }

  const handleSelectChange = async (rowIdx, col, val) => {
    if (!data) return
    const row = data.rows[rowIdx]
    const pk = row[data.pk_column]
    // 哨兵值 __none__ → null（数据库 NULL）
    const sendVal = (val === '' || val === '__none__') ? null : val
    setSavingCell(true)
    try {
      const res = await api.updateTableRow(data.table, data.pk_column, pk, { [col]: sendVal })
      if (res?.status === 'ok') {
        // 局部更新行
        const newRows = [...data.rows]
        newRows[rowIdx] = { ...row, [col]: sendVal }
        setData({ ...data, rows: newRows })
        setEditingCell(null)
        showToast('已保存', 'ok')
      } else {
        showToast(res?.message || '保存失败', 'err')
      }
    } catch (e) {
      showToast('保存失败：' + (e?.message || e), 'err')
    }
    setSavingCell(false)
  }

  return (
    <div>
      {toast && (
        <div style={{
          position: 'fixed', top: 70, right: 24, zIndex: 100,
          padding: '8px 16px', fontSize: 12,
          background: toast.type === 'ok' ? 'var(--up)' : 'var(--down)',
          color: '#fff', fontFamily: '"GeistMono", monospace',
        }}>{toast.msg}</div>
      )}

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
            {editableSet.size > 0 && (
              <span style={{ marginLeft: 12, color: 'var(--accent)' }}>
                · 可编辑列：{[...editableSet].join(', ')}
              </span>
            )}
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
                    <th key={col} style={editableSet.has(col) ? { color: 'var(--accent)' } : {}}>
                      {col}{editableSet.has(col) ? ' ✎' : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, i) => (
                  <tr key={i}>
                    {data.columns.map(col => {
                      const isEditable = editableSet.has(col)
                      const isEditing = editingCell?.rowIdx === i && editingCell?.col === col
                      const cellStyle = {
                        maxWidth: 200,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        fontFamily: typeof row[col] === 'number' ? '"GeistMono", monospace' : 'inherit',
                      }
                      if (!isEditable) {
                        return (
                          <td key={col} style={cellStyle} title={String(row[col] ?? '')}>
                            {row[col] == null ? '-' : String(row[col])}
                          </td>
                        )
                      }
                      // 可编辑：下拉框
                      const opts = options[col] || []
                      return (
                        <td
                          key={col}
                          style={{ ...cellStyle, padding: '2px 6px', cursor: 'pointer', background: isEditing ? 'var(--bg-raised)' : 'transparent' }}
                          onClick={() => !isEditing && !savingCell && setEditingCell({ rowIdx: i, col })}
                        >
                          {isEditing ? (
                            <select
                              autoFocus
                              disabled={savingCell}
                              defaultValue={row[col] || '__none__'}
                              onBlur={e => {
                                if (e.target.value !== (row[col] || '__none__')) {
                                  handleSelectChange(i, col, e.target.value)
                                } else {
                                  setEditingCell(null)
                                }
                              }}
                              onChange={e => e.target.value !== (row[col] || '') && handleSelectChange(i, col, e.target.value)}
                              style={{
                                width: '100%', padding: '2px 4px',
                                background: 'var(--bg)', color: 'var(--text)',
                                border: '1px solid var(--accent)',
                                fontSize: 12, fontFamily: 'inherit',
                              }}
                            >
                              {opts.map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                              ))}
                            </select>
                          ) : (
                            <span title="点击修改" style={{ color: row[col] ? 'var(--text)' : 'var(--text-muted)' }}>
                              {row[col]
                                ? (opts.find(o => o.value === row[col])?.label || row[col])
                                : (col === 'type2' ? '其他' : '— 点击 —')}
                            </span>
                          )}
                        </td>
                      )
                    })}
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
