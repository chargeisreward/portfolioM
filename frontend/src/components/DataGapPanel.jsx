import React, { useEffect, useState } from 'react'
import { getGapReport, fixGap, setIndexClassification } from '../api'

/**
 * 数据补足页面（仅 admin 可见）
 * 3 个 tab：个股报告 / 指数构成 / 指数分类
 */
const TABS = [
  { key: 'stock_report', label: '个股报告' },
  { key: 'index_constituent', label: '指数构成' },
  { key: 'index_classification', label: '指数分类' },
]

export default function DataGapPanel() {
  const [tab, setTab] = useState('stock_report')
  const [data, setData] = useState({ items: [], counts: { OPEN: 0, FIXED: 0 } })
  const [editing, setEditing] = useState(null)
  const [busy, setBusy] = useState(false)

  function refresh() {
    getGapReport({ gap_type: tab, status: 'OPEN' }).then(setData).catch(() => setData({ items: [], counts: { OPEN: 0, FIXED: 0 } }))
  }
  useEffect(refresh, [tab])

  async function handleFix(g) {
    if (g.gap_type === 'index_classification') {
      setEditing({ id: g.id, index_code: g.index_code, category: '', theme: '' })
      return
    }
    if (!confirm(`确认修复 #${g.id}？`)) return
    setBusy(true)
    try {
      await fixGap(g.id)
      refresh()
    } catch (e) {
      alert(e?.response?.data?.detail || '修复失败')
    }
    setBusy(false)
  }

  async function submitClassification() {
    if (!editing) return
    setBusy(true)
    try {
      await setIndexClassification({
        index_code: editing.index_code,
        category: editing.category,
        theme: editing.theme,
      })
      await fixGap(editing.id)
      setEditing(null)
      refresh()
    } catch (e) {
      alert(e?.response?.data?.detail || '保存失败')
    }
    setBusy(false)
  }

  return (
    <div style={{ padding: 24 }}>
      <h2>
        数据补足
        <span style={{ fontSize: 14, color: 'var(--text-muted)', marginLeft: 12 }}>
          OPEN: {data.counts.OPEN} · FIXED: {data.counts.FIXED}
        </span>
      </h2>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, marginTop: 8 }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              padding: '6px 14px',
              border: '1px solid var(--border)',
              background: tab === t.key ? 'var(--accent, #6366f1)' : 'transparent',
              color: tab === t.key ? '#fff' : 'var(--text)',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >{t.label}</button>
        ))}
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
            {tab === 'stock_report' && <><th style={{ padding: 8 }}>客户ID</th><th style={{ padding: 8 }}>股票代码</th><th style={{ padding: 8 }}>描述</th></>}
            {tab === 'index_constituent' && <><th style={{ padding: 8 }}>指数代码</th><th style={{ padding: 8 }}>缺失日期</th><th style={{ padding: 8 }}>描述</th></>}
            {tab === 'index_classification' && <><th style={{ padding: 8 }}>指数代码</th><th style={{ padding: 8 }}>描述</th></>}
            <th style={{ padding: 8 }}>检测时间</th>
            <th style={{ padding: 8 }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {data.items.length === 0 && (
            <tr><td colSpan="5" style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
              暂无 {TABS.find(t => t.key === tab)?.label} 缺口 ✓
            </td></tr>
          )}
          {data.items.map(g => (
            <tr key={g.id} style={{ borderBottom: '1px solid var(--border)' }}>
              {tab === 'stock_report' && <>
                <td style={{ padding: 8 }}>{g.user_id}</td>
                <td style={{ padding: 8, fontFamily: 'monospace' }}>{g.stock_code}</td>
                <td style={{ padding: 8 }}>{g.description}</td>
              </>}
              {tab === 'index_constituent' && <>
                <td style={{ padding: 8, fontFamily: 'monospace' }}>{g.index_code}</td>
                <td style={{ padding: 8 }}>{g.as_of_date}</td>
                <td style={{ padding: 8 }}>{g.description}</td>
              </>}
              {tab === 'index_classification' && <>
                <td style={{ padding: 8, fontFamily: 'monospace' }}>{g.index_code}</td>
                <td style={{ padding: 8 }}>{g.description}</td>
              </>}
              <td style={{ padding: 8, fontSize: 11, color: 'var(--text-muted)' }}>
                {g.detected_at?.slice(0, 16)}
              </td>
              <td style={{ padding: 8 }}>
                <button onClick={() => handleFix(g)} disabled={busy} style={{ padding: '2px 10px', fontSize: 11 }}>
                  {tab === 'index_classification' ? '录入分类' : '立即修复'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div style={{
            background: 'var(--bg, #fff)', padding: 24, borderRadius: 8,
            minWidth: 360, border: '1px solid var(--border)',
          }}>
            <h3 style={{ marginTop: 0 }}>编辑分类 - {editing.index_code}</h3>
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>大类 (category)</label>
              <input
                value={editing.category}
                onChange={e => setEditing({ ...editing, category: e.target.value })}
                style={{ width: '100%', padding: 6, marginTop: 4, boxSizing: 'border-box' }}
                placeholder="如：宽基 / 行业 / 主题"
              />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)' }}>主题 (theme)</label>
              <input
                value={editing.theme}
                onChange={e => setEditing({ ...editing, theme: e.target.value })}
                style={{ width: '100%', padding: 6, marginTop: 4, boxSizing: 'border-box' }}
                placeholder="如：大盘 / 科技 / 消费"
              />
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setEditing(null)}>取消</button>
              <button onClick={submitClassification} disabled={busy} style={{ background: 'var(--accent, #6366f1)', color: '#fff', border: 'none', padding: '6px 16px' }}>
                {busy ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
