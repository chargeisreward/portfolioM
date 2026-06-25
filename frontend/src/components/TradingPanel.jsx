import React, { useState, useEffect, useCallback } from 'react'
import * as api from '../api'
import { postImport } from '../api'

/**
 * 交易维护面板。
 *
 * 布局：交易维护 → 待确认表（解析后，input 编辑格式）→ 历史交易列表（文本表格，点击编辑才变 input）
 *
 * 用户需求（2026-06-26）：
 * 1. 待确认表：input 编辑格式，正负值高亮（亮蓝/亮红），日期列拉宽
 * 2. 历史交易列表：默认文本表格（数字右对齐+2位小数+千分位），点击编辑才变 input
 * 3. 操作用图标按钮：编辑✏️ / 删除🗑️ / 保存✓ / 取消✕
 * 4. 深色背景下颜色更亮：正值 #60a5fa，负值 #f87171
 */

// 交易类型中文映射
const TRADE_TYPE_LABELS = { buy: '申购', sell: '赎回', dividend: '分红', others: '其他' }

// 证券状态中文映射
const SECURITY_STATUS_LABELS = {
  exists: { text: '✓已验证', color: 'var(--up)' },
  new_verified: { text: '新入库', color: 'var(--text-secondary)' },
  new_unverified: { text: '⚠未验证', color: 'var(--down)' },
  failed: { text: '⚠未验证', color: 'var(--down)' },
}

// 正负值颜色：深色背景下更亮的蓝/红
const valueColor = (v) => {
  const n = Number(v) || 0
  if (n > 0) return '#60a5fa'   // blue-400，深色背景下亮蓝
  if (n < 0) return '#f87171'   // red-400，深色背景下亮红
  return 'inherit'
}
const valueFontWeight = (v) => {
  const n = Number(v) || 0
  return n !== 0 ? 600 : 400
}

// 数字格式化：固定2位小数 + 千分位逗号
const fmtNum = (v, decimals = 2) => {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

// 数字单元格样式：右对齐 + 等宽数字
const numStyle = { textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

// ---- 图标按钮组件 ----
const IconBtn = ({ path, onClick, title, disabled, color = 'var(--text-secondary)' }) => (
  <button
    onClick={onClick}
    title={title}
    disabled={disabled}
    style={{
      border: 'none',
      background: 'transparent',
      cursor: disabled ? 'wait' : 'pointer',
      padding: '2px 4px',
      color: disabled ? 'var(--text-muted)' : color,
      display: 'inline-flex',
      alignItems: 'center',
      lineHeight: 0,
    }}
  >
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  </button>
)

// 图标 path（feather icon 风格）
const ICONS = {
  edit: 'M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7 M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z',
  trash: 'M3 6h18 M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2 M10 11v6 M14 11v6',
  check: 'M5 13l4 4L19 7',
  x: 'M18 6L6 18 M6 6l12 12',
}

export default function TradingPanel() {
  // ---- 交易维护 state ----
  const [rawText, setRawText] = useState('')
  const [parsedTrades, setParsedTrades] = useState([])
  const [parsing, setParsing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [parseError, setParseError] = useState(null)

  // ---- 历史交易列表 state ----
  const [historyTrades, setHistoryTrades] = useState([])
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [editingId, setEditingId] = useState(null)      // 正在编辑的行 id
  const [editBuffer, setEditBuffer] = useState(null)     // 编辑中的行数据副本
  const [savingId, setSavingId] = useState(null)         // 正在保存/删除的交易 id

  // ---- 加载历史交易列表 ----
  const loadHistory = useCallback(() => {
    setLoadingHistory(true)
    api.getTrades().then(data => {
      setHistoryTrades(data || [])
    }).catch(() => {
      setHistoryTrades([])
    }).finally(() => setLoadingHistory(false))
  }, [])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  // ---- 解析交易 ----
  const handleParse = async () => {
    if (!rawText.trim()) {
      alert('请粘贴交易记录文本')
      return
    }
    setParsing(true)
    setParseError(null)
    try {
      const result = await api.parseTrades(rawText)
      if (result.parse_error) {
        setParseError(result.parse_error)
        setParsedTrades(result.trades || [])
      } else {
        setParsedTrades(result.trades || [])
      }
    } catch (e) {
      setParseError(e?.response?.data?.detail || e.message)
    } finally {
      setParsing(false)
    }
  }

  // ---- 编辑待确认表某行字段 ----
  const handleTradeEdit = (idx, field, value) => {
    setParsedTrades(prev => prev.map((t, i) => i === idx ? { ...t, [field]: value } : t))
  }

  // ---- 提交交易 ----
  const handleConfirm = async () => {
    if (!parsedTrades.length) {
      alert('无交易可提交')
      return
    }
    setConfirming(true)
    try {
      const result = await api.confirmTrades(parsedTrades)
      alert(`提交成功：确认 ${result.confirmed_count} 笔交易，已重算持仓`)
      setRawText('')
      setParsedTrades([])
      setParseError(null)
      loadHistory()
    } catch (e) {
      alert(`提交失败：${e?.response?.data?.detail || e.message}`)
    } finally {
      setConfirming(false)
    }
  }

  // ---- 导入持仓 Excel（兜底）----
  const handleImport = async () => {
    if (!confirm('从 dataDir 导入最新 Excel 持仓？这将覆盖当前持仓表。')) return
    try {
      const r = await postImport()
      alert(`导入完成：${r.message || r.count || ''}`)
    } catch (e) {
      alert(`导入失败：${e?.response?.data?.detail || e.message}`)
    }
  }

  // ---- 历史交易：进入编辑模式 ----
  const handleEditStart = (t) => {
    setEditingId(t.id)
    setEditBuffer({ ...t })
  }

  // ---- 历史交易：编辑中字段变更 ----
  const handleEditChange = (field, value) => {
    setEditBuffer(prev => ({ ...prev, [field]: value }))
  }

  // ---- 历史交易：取消编辑 ----
  const handleEditCancel = () => {
    setEditingId(null)
    setEditBuffer(null)
  }

  // ---- 历史交易：保存编辑 ----
  const handleEditSave = async () => {
    if (!editBuffer) return
    setSavingId(editingId)
    try {
      await api.updateTrade(editingId, {
        trade_date: editBuffer.trade_date,
        security_code: editBuffer.security_code,
        security_name: editBuffer.security_name,
        trade_type: editBuffer.trade_type,
        confirmed_shares: editBuffer.confirmed_shares,
        confirmed_amount: editBuffer.confirmed_amount,
        nav_price: editBuffer.nav_price,
        nav_date: editBuffer.nav_date,
        fee: editBuffer.fee,
        remarks: editBuffer.remarks,
      })
      setEditingId(null)
      setEditBuffer(null)
      loadHistory()
    } catch (e) {
      alert(`保存失败：${e?.response?.data?.detail || e.message}`)
    } finally {
      setSavingId(null)
    }
  }

  // ---- 历史交易：删除 ----
  const handleDelete = async (id) => {
    if (!confirm('确认删除该交易记录？删除后将触发全量重算。')) return
    setSavingId(id)
    try {
      await api.deleteTrade(id)
      loadHistory()
    } catch (e) {
      alert(`删除失败：${e?.response?.data?.detail || e.message}`)
    } finally {
      setSavingId(null)
    }
  }

  return (
    <div>
      {/* ==================== 交易维护 ==================== */}
      <div className="raised" style={{ padding: 16, marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 12 }}>交易维护</div>

        {/* 粘贴区域 */}
        <div style={{ marginBottom: 8 }}>
          <textarea
            value={rawText}
            onChange={e => setRawText(e.target.value)}
            placeholder="粘贴交易记录文本（支持多行，LLM 自动解析）&#10;示例：&#10;2025-07-20 申购 510300.SH 沪深300ETF 1000份 4500元&#10;2025-07-21 赎回 159919.SZ 300ETF 500份 2300元"
            style={{
              width: '100%',
              minHeight: 120,
              padding: '8px 10px',
              border: '1px solid var(--border)',
              borderRadius: 4,
              fontFamily: 'inherit',
              fontSize: 13,
              lineHeight: 1.6,
              resize: 'vertical',
              boxSizing: 'border-box',
              background: 'var(--bg-secondary)',
            }}
          />
        </div>

        {/* 解析按钮 */}
        <div>
          <button
            onClick={handleParse}
            disabled={parsing}
            className="btn-ghost"
            style={{ padding: '6px 16px', cursor: parsing ? 'wait' : 'pointer' }}
          >
            {parsing ? '解析中...' : '解析交易'}
          </button>
          <button
            onClick={handleImport}
            className="btn-ghost"
            style={{ padding: '6px 16px', marginLeft: 8, cursor: 'pointer' }}
          >
            导入持仓 Excel（兜底）
          </button>
        </div>

        {/* 解析错误 */}
        {parseError && (
          <div style={{ color: 'var(--down)', fontSize: 12, marginTop: 8 }}>
            解析错误：{parseError}
          </div>
        )}
      </div>

      {/* ==================== 待确认表（解析后显示，提交后清空）— input 编辑格式 ==================== */}
      {parsedTrades.length > 0 && (
        <div className="raised" style={{ padding: 16, marginBottom: 16 }}>
          <div className="section-title" style={{ marginBottom: 12 }}>待确认交易</div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
            交易记录待确认表（可编辑，确认后点击「提交」）
          </div>
          <div className="table-wrap" style={{ marginBottom: 12 }}>
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 140 }}>日期</th>
                  <th style={{ width: 110 }}>代码</th>
                  <th>名称</th>
                  <th style={{ width: 70 }}>类型</th>
                  <th style={{ width: 100 }}>确认份额</th>
                  <th style={{ width: 100 }}>确认金额</th>
                  <th style={{ width: 80 }}>状态</th>
                </tr>
              </thead>
              <tbody>
                {parsedTrades.map((t, idx) => {
                  const statusInfo = SECURITY_STATUS_LABELS[t.security_status] || SECURITY_STATUS_LABELS.exists
                  return (
                    <tr key={idx}>
                      <td>
                        <input
                          type="date"
                          className="ig"
                          value={t.trade_date || ''}
                          onChange={e => handleTradeEdit(idx, 'trade_date', e.target.value)}
                          style={{ width: 130 }}
                        />
                      </td>
                      <td>
                        <input
                          className="ig"
                          value={t.security_code || ''}
                          onChange={e => handleTradeEdit(idx, 'security_code', e.target.value)}
                          style={{ width: 100 }}
                        />
                      </td>
                      <td>
                        <input
                          className="ig"
                          value={t.security_name || ''}
                          onChange={e => handleTradeEdit(idx, 'security_name', e.target.value)}
                          style={{ width: '100%' }}
                        />
                      </td>
                      <td>
                        <select
                          className="ig"
                          value={t.trade_type || 'buy'}
                          onChange={e => handleTradeEdit(idx, 'trade_type', e.target.value)}
                          style={{ width: 65 }}
                        >
                          {Object.entries(TRADE_TYPE_LABELS).map(([k, v]) => (
                            <option key={k} value={k}>{v}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          type="number"
                          className="ig"
                          value={t.confirmed_shares ?? 0}
                          onChange={e => handleTradeEdit(idx, 'confirmed_shares', parseFloat(e.target.value) || 0)}
                          style={{ width: 90, color: valueColor(t.confirmed_shares), fontWeight: valueFontWeight(t.confirmed_shares) }}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          className="ig"
                          value={t.confirmed_amount ?? 0}
                          onChange={e => handleTradeEdit(idx, 'confirmed_amount', parseFloat(e.target.value) || 0)}
                          style={{ width: 90, color: valueColor(t.confirmed_amount), fontWeight: valueFontWeight(t.confirmed_amount) }}
                        />
                      </td>
                      <td style={{ color: statusInfo.color, fontSize: 11 }}>
                        {statusInfo.text}
                        {t.security_message && (
                          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{t.security_message}</div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <button
            onClick={handleConfirm}
            disabled={confirming}
            className="cur-btn"
            style={{ padding: '8px 24px', cursor: confirming ? 'wait' : 'pointer' }}
          >
            {confirming ? '提交中...' : `提交 ${parsedTrades.length} 笔交易`}
          </button>
        </div>
      )}

      {/* ==================== 历史交易列表 — 默认文本表格，点击编辑才变 input ==================== */}
      <div className="raised" style={{ padding: 16 }}>
        <div className="section-title" style={{ marginBottom: 12 }}>历史交易列表</div>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 6 }}>
          点击 ✏️ 编辑，点击 🗑️ 删除（均触发全量重算）
        </div>
        {loadingHistory ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>加载中...</div>
        ) : historyTrades.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>暂无历史交易</div>
        ) : (
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: 'auto' }}>
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 120 }}>日期</th>
                  <th style={{ width: 110 }}>代码</th>
                  <th>名称</th>
                  <th style={{ width: 60 }}>类型</th>
                  <th style={{ width: 110, ...numStyle }}>确认份额</th>
                  <th style={{ width: 110, ...numStyle }}>确认金额</th>
                  <th style={{ width: 90, ...numStyle }}>净值</th>
                  <th style={{ width: 70 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {historyTrades.map((t) => {
                  const isEditing = editingId === t.id

                  // ---- 编辑模式：input ----
                  if (isEditing) {
                    return (
                      <tr key={t.id} style={{ background: 'var(--bg-secondary)' }}>
                        <td>
                          <input
                            type="date"
                            className="ig"
                            value={editBuffer.trade_date || ''}
                            onChange={e => handleEditChange('trade_date', e.target.value)}
                            style={{ width: 130 }}
                          />
                        </td>
                        <td>
                          <input
                            className="ig"
                            value={editBuffer.security_code || ''}
                            onChange={e => handleEditChange('security_code', e.target.value)}
                            style={{ width: 100 }}
                          />
                        </td>
                        <td>
                          <input
                            className="ig"
                            value={editBuffer.security_name || ''}
                            onChange={e => handleEditChange('security_name', e.target.value)}
                            style={{ width: '100%' }}
                          />
                        </td>
                        <td>
                          <select
                            className="ig"
                            value={editBuffer.trade_type || 'buy'}
                            onChange={e => handleEditChange('trade_type', e.target.value)}
                            style={{ width: 60 }}
                          >
                            {Object.entries(TRADE_TYPE_LABELS).map(([k, v]) => (
                              <option key={k} value={k}>{v}</option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <input
                            type="number"
                            className="ig"
                            value={editBuffer.confirmed_shares ?? 0}
                            onChange={e => handleEditChange('confirmed_shares', parseFloat(e.target.value) || 0)}
                            style={{ width: 100, color: valueColor(editBuffer.confirmed_shares), fontWeight: valueFontWeight(editBuffer.confirmed_shares), textAlign: 'right' }}
                          />
                        </td>
                        <td>
                          <input
                            type="number"
                            className="ig"
                            value={editBuffer.confirmed_amount ?? 0}
                            onChange={e => handleEditChange('confirmed_amount', parseFloat(e.target.value) || 0)}
                            style={{ width: 100, color: valueColor(editBuffer.confirmed_amount), fontWeight: valueFontWeight(editBuffer.confirmed_amount), textAlign: 'right' }}
                          />
                        </td>
                        <td>
                          <input
                            type="number"
                            className="ig"
                            value={editBuffer.nav_price ?? ''}
                            onChange={e => handleEditChange('nav_price', e.target.value ? parseFloat(e.target.value) : null)}
                            style={{ width: 80, textAlign: 'right' }}
                          />
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <IconBtn
                            path={ICONS.check}
                            onClick={handleEditSave}
                            title="保存"
                            disabled={savingId === t.id}
                            color="var(--up)"
                          />
                          <IconBtn
                            path={ICONS.x}
                            onClick={handleEditCancel}
                            title="取消"
                            disabled={savingId === t.id}
                          />
                        </td>
                      </tr>
                    )
                  }

                  // ---- 展示模式：文本表格 ----
                  return (
                    <tr key={t.id}>
                      <td>{t.trade_date || '—'}</td>
                      <td>{t.security_code}</td>
                      <td>{t.security_name || '—'}</td>
                      <td>{TRADE_TYPE_LABELS[t.trade_type] || t.trade_type}</td>
                      <td style={{ ...numStyle, color: valueColor(t.confirmed_shares), fontWeight: valueFontWeight(t.confirmed_shares) }}>
                        {fmtNum(t.confirmed_shares)}
                      </td>
                      <td style={{ ...numStyle, color: valueColor(t.confirmed_amount), fontWeight: valueFontWeight(t.confirmed_amount) }}>
                        {fmtNum(t.confirmed_amount)}
                      </td>
                      <td style={numStyle}>{t.nav_price != null ? fmtNum(t.nav_price, 4) : '—'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <IconBtn
                          path={ICONS.edit}
                          onClick={() => handleEditStart(t)}
                          title="编辑"
                          disabled={savingId !== null}
                        />
                        <IconBtn
                          path={ICONS.trash}
                          onClick={() => handleDelete(t.id)}
                          title="删除"
                          disabled={savingId !== null}
                          color="var(--down)"
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
