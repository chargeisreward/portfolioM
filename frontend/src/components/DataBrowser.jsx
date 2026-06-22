import React, { useState, useEffect } from 'react'
import * as api from '../api'

/**
 * 数据页面：三标签页数据质量仪表盘
 * 1. 完整性 - 所有表的行数/日期范围/填充率
 * 2. 宽度 - 选中表的字段级统计（非空率/唯一值/min/max/avg）
 * 3. 结构 - 所有表的 schema（字段名/类型/可空/主键/约束）
 */
export default function DataBrowser() {
  const [tab, setTab] = useState('completeness')

  return (
    <div>
      {/* 顶部标签页切换 */}
      <div style={{ display: 'flex', gap: 0, marginBottom: 12, borderBottom: '1px solid var(--border)' }}>
        {[
          { id: 'completeness', label: '完整性' },
          { id: 'width', label: '宽度' },
          { id: 'structure', label: '结构' },
        ].map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: '8px 20px',
              background: tab === t.id ? 'var(--bg-raised)' : 'transparent',
              border: 'none',
              borderBottom: tab === t.id ? '2px solid var(--text)' : '2px solid transparent',
              color: tab === t.id ? 'var(--text)' : 'var(--text-muted)',
              cursor: 'pointer',
              fontSize: 12,
              fontFamily: '"GeistMono", monospace',
              letterSpacing: 0.5,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'completeness' && <CompletenessTab />}
      {tab === 'width' && <WidthTab />}
      {tab === 'structure' && <StructureTab />}
    </div>
  )
}

// ============================================================
// 标签页 1：完整性 - 所有表的数据完整程度
// ============================================================
function CompletenessTab() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    api.getDataOverview()
      .then(d => { setData(d); setLoading(false) })
      .catch(e => { setError('加载失败：' + (e?.message || e)); setLoading(false) })
  }, [])

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>加载中...</div>
  if (error) return <div className="raised" style={{ borderColor: 'var(--down)', color: 'var(--down)' }}>{error}</div>
  if (!data) return null

  const { summary, tables } = data

  return (
    <div>
      {/* 汇总卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 12 }}>
        <SummaryCard label="总表数" value={summary.total_tables} color="var(--text)" />
        <SummaryCard label="有数据" value={summary.non_empty} color="var(--chart-up)" />
        <SummaryCard label="空表" value={summary.empty} color="var(--chart-down)" />
        <SummaryCard label="平均填充率" value={summary.avg_fill_rate + '%'} color="var(--accent-primary)" />
      </div>

      {/* 表格 */}
      <div className="raised" style={{ padding: 0, overflow: 'hidden' }}>
        <div style={{ maxHeight: 600, overflow: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>分类</th>
                <th>表名</th>
                <th>描述</th>
                <th style={{ textAlign: 'right' }}>行数</th>
                <th style={{ textAlign: 'right' }}>字段数</th>
                <th>日期字段</th>
                <th>日期范围</th>
                <th>最后更新</th>
                <th style={{ textAlign: 'right' }}>填充率</th>
              </tr>
            </thead>
            <tbody>
              {tables.map(t => {
                const avgFill = t.fill_rates && Object.keys(t.fill_rates).length > 0
                  ? (Object.values(t.fill_rates).reduce((a, b) => a + b, 0) / Object.keys(t.fill_rates).length).toFixed(1)
                  : '-'
                const fillColor = avgFill === '-' ? 'var(--text-muted)' : (parseFloat(avgFill) > 80 ? 'var(--chart-up)' : parseFloat(avgFill) > 50 ? 'var(--text-secondary)' : 'var(--chart-down)')
                return (
                  <tr key={t.table} style={{ opacity: t.row_count === 0 ? 0.5 : 1 }}>
                    <td style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>{t.category}</td>
                    <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, color: 'var(--text)' }}>{t.label}</td>
                    <td style={{ fontSize: 10, color: 'var(--text-secondary)' }}>{t.desc}</td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace', fontSize: 11, color: t.row_count > 0 ? 'var(--text)' : 'var(--text-muted)' }}>
                      {t.row_count.toLocaleString()}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace', fontSize: 11, color: 'var(--text-muted)' }}>{t.column_count}</td>
                    <td style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>
                      {t.date_range?.field || '-'}
                    </td>
                    <td style={{ fontSize: 10, color: 'var(--text-secondary)', fontFamily: '"GeistMono", monospace' }}>
                      {t.date_range ? `${t.date_range.min || '?'} ~ ${t.date_range.max || '?'}` : '-'}
                    </td>
                    <td style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>
                      {t.last_update ? t.last_update.substring(0, 19) : '-'}
                    </td>
                    <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace', fontSize: 11, color: fillColor, fontWeight: 600 }}>
                      {avgFill === '-' ? '-' : avgFill + '%'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// 汇总卡片组件
function SummaryCard({ label, value, color }) {
  return (
    <div className="raised" style={{ padding: '12px 16px', textAlign: 'center' }}>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color, fontFamily: '"GeistMono", monospace' }}>{value}</div>
    </div>
  )
}

// ============================================================
// 标签页 2：宽度 - 字段级统计
// ============================================================
function WidthTab() {
  const [overview, setOverview] = useState(null)
  const [selectedTable, setSelectedTable] = useState('')
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)

  // 加载表列表（复用 overview 数据）
  useEffect(() => {
    api.getDataOverview().then(d => {
      setOverview(d)
      // 默认选第一个有数据的表
      const firstNonEmpty = d.tables.find(t => t.row_count > 0)
      if (firstNonEmpty) setSelectedTable(firstNonEmpty.table)
    }).catch(() => {})
  }, [])

  // 加载选中表的字段统计
  useEffect(() => {
    if (!selectedTable) return
    setLoading(true)
    api.getTableStats(selectedTable)
      .then(d => { setStats(d); setLoading(false) })
      .catch(() => { setStats(null); setLoading(false) })
  }, [selectedTable])

  if (!overview) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>加载中...</div>

  // 按分类分组
  const grouped = {}
  overview.tables.forEach(t => {
    if (!grouped[t.category]) grouped[t.category] = []
    grouped[t.category].push(t)
  })

  return (
    <div style={{ display: 'flex', gap: 8 }}>
      {/* 左侧：表选择器 */}
      <div className="raised" style={{ width: 240, padding: 0, maxHeight: 700, overflow: 'auto' }}>
        <div style={{ padding: '8px 12px', fontSize: 10, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          数据表（{overview.tables.length}）
        </div>
        {Object.entries(grouped).map(([cat, tables]) => (
          <div key={cat}>
            <div style={{ padding: '6px 12px', fontSize: 9, color: 'var(--text-muted)', background: 'var(--bg)', fontWeight: 600 }}>
              {cat}
            </div>
            {tables.map(t => (
              <button
                key={t.table}
                onClick={() => setSelectedTable(t.table)}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  width: '100%', padding: '6px 12px',
                  background: selectedTable === t.table ? 'var(--bg-raised)' : 'transparent',
                  border: 'none', borderLeft: selectedTable === t.table ? '2px solid var(--text)' : '2px solid transparent',
                  cursor: 'pointer', fontSize: 11,
                  color: selectedTable === t.table ? 'var(--text)' : 'var(--text-secondary)',
                  fontFamily: '"GeistMono", monospace',
                }}
              >
                <span>{t.label}</span>
                <span style={{ fontSize: 9, color: t.row_count > 0 ? 'var(--text-muted)' : 'var(--chart-down)' }}>
                  {t.row_count.toLocaleString()}
                </span>
              </button>
            ))}
          </div>
        ))}
      </div>

      {/* 右侧：字段统计 */}
      <div className="raised" style={{ flex: 1, padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>加载中...</div>
        ) : stats && stats.fields ? (
          <>
            <div style={{ padding: '8px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 12, fontWeight: 600, fontFamily: '"GeistMono", monospace' }}>
                {stats.table} · {stats.total_rows.toLocaleString()} 行
              </span>
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{stats.fields.length} 个字段</span>
            </div>
            <div style={{ maxHeight: 650, overflow: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>字段名</th>
                    <th>类型</th>
                    <th style={{ textAlign: 'right' }}>非空率</th>
                    <th style={{ textAlign: 'right' }}>唯一值</th>
                    <th style={{ textAlign: 'right' }}>min</th>
                    <th style={{ textAlign: 'right' }}>max</th>
                    <th style={{ textAlign: 'right' }}>avg</th>
                    <th>示例值</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.fields.map(f => (
                    <tr key={f.name}>
                      <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11 }}>
                        {f.primary_key && <span style={{ color: 'var(--accent-primary)', marginRight: 4 }}>PK</span>}
                        {f.name}
                      </td>
                      <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>{f.type}</td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 4 }}>
                          <div style={{ width: 40, height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                            <div style={{
                              width: f.fill_rate + '%', height: '100%',
                              background: f.fill_rate > 80 ? 'var(--chart-up)' : f.fill_rate > 50 ? 'var(--text-secondary)' : 'var(--chart-down)',
                            }} />
                          </div>
                          <span style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: f.fill_rate > 80 ? 'var(--chart-up)' : f.fill_rate > 50 ? 'var(--text-secondary)' : 'var(--chart-down)' }}>
                            {f.fill_rate}%
                          </span>
                        </div>
                      </td>
                      <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                        {f.distinct_count.toLocaleString()}
                      </td>
                      <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                        {f.min != null ? String(f.min).substring(0, 20) : '-'}
                      </td>
                      <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                        {f.max != null ? String(f.max).substring(0, 20) : '-'}
                      </td>
                      <td style={{ textAlign: 'right', fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                        {f.avg != null ? f.avg : '-'}
                      </td>
                      <td style={{ fontSize: 10, color: 'var(--text-secondary)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={f.sample || ''}>
                        {f.sample || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>暂无数据</div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// 标签页 3：结构 - 所有表的 schema
// ============================================================
function StructureTab() {
  const [schema, setSchema] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('全部')
  const [expanded, setExpanded] = useState({})

  useEffect(() => {
    setLoading(true)
    api.getDataSchema()
      .then(d => { setSchema(d); setLoading(false) })
      .catch(() => { setLoading(false) })
  }, [])

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>加载中...</div>
  if (!schema) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 }}>暂无数据</div>

  // 分类列表
  const categories = ['全部', ...new Set(Object.values(schema).map(s => s.category))]

  // 过滤后的表
  const tables = Object.entries(schema).filter(([_, s]) => filter === '全部' || s.category === filter)

  const toggle = (table) => setExpanded(prev => ({ ...prev, [table]: !prev[table] }))

  return (
    <div>
      {/* 分类筛选器 */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace', marginRight: 4 }}>分类</span>
        {categories.map(c => (
          <button
            key={c}
            onClick={() => setFilter(c)}
            className={filter === c ? 'cur-btn on' : 'cur-btn'}
            style={{ fontSize: 10 }}
          >
            {c}
          </button>
        ))}
      </div>

      {/* 可折叠的表列表 */}
      <div className="raised" style={{ padding: 0, maxHeight: 700, overflow: 'auto' }}>
        {tables.map(([tableName, s]) => (
          <div key={tableName} style={{ borderBottom: '1px solid var(--border)' }}>
            {/* 表头（可点击展开） */}
            <button
              onClick={() => toggle(tableName)}
              style={{
                display: 'flex', alignItems: 'center', width: '100%',
                padding: '8px 16px', background: 'transparent', border: 'none',
                cursor: 'pointer', textAlign: 'left',
              }}
            >
              <span style={{ marginRight: 8, color: 'var(--text-muted)', fontSize: 10, fontFamily: '"GeistMono", monospace' }}>
                {expanded[tableName] ? '▼' : '▶'}
              </span>
              <span style={{ fontFamily: '"GeistMono", monospace', fontSize: 12, fontWeight: 600, color: 'var(--text)', flex: 1 }}>
                {s.label}
              </span>
              <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace', marginRight: 12 }}>
                {tableName}
              </span>
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{s.fields.length} 字段</span>
            </button>

            {/* 展开内容：字段详情 */}
            {expanded[tableName] && (
              <div style={{ padding: '0 16px 12px 32px', background: 'var(--bg)' }}>
                {s.desc && (
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8, fontStyle: 'italic' }}>
                    {s.desc}
                  </div>
                )}
                <table className="data-table" style={{ fontSize: 10 }}>
                  <thead>
                    <tr>
                      <th>字段名</th>
                      <th>类型</th>
                      <th>可空</th>
                      <th>主键</th>
                      <th>默认值</th>
                      <th>自增</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.fields.map(f => (
                      <tr key={f.name}>
                        <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, color: 'var(--text)' }}>
                          {f.name}
                        </td>
                        <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                          {f.type}
                        </td>
                        <td style={{ fontSize: 10, color: f.nullable ? 'var(--text-muted)' : 'var(--chart-down)' }}>
                          {f.nullable ? 'YES' : 'NO'}
                        </td>
                        <td style={{ fontSize: 10, color: f.primary_key ? 'var(--accent-primary)' : 'var(--text-muted)' }}>
                          {f.primary_key ? 'PK' : '-'}
                        </td>
                        <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                          {f.default || '-'}
                        </td>
                        <td style={{ fontSize: 10, color: f.autoincrement ? 'var(--text-secondary)' : 'var(--text-muted)' }}>
                          {f.autoincrement ? '是' : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {/* 唯一约束 */}
                {s.uniques && s.uniques.length > 0 && (
                  <div style={{ marginTop: 8, fontSize: 10, color: 'var(--text-muted)' }}>
                    <span style={{ fontFamily: '"GeistMono", monospace' }}>唯一约束：</span>
                    {s.uniques.map((u, i) => (
                      <span key={i} style={{
                        display: 'inline-block', padding: '1px 6px', margin: '2px 4px 2px 0',
                        border: '1px solid var(--border)', fontSize: 9,
                        fontFamily: '"GeistMono", monospace',
                      }}>
                        {u.columns.join('+')}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
