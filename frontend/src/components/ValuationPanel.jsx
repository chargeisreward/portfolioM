import React, { useState, useEffect, useCallback } from 'react'
import * as api from '../api'

/**
 * 估值表面板（独立页面）。
 *
 * 从 TradingPanel 平移而来：日期控件 → KPI 占位 + 当日交易 + 持仓快照。
 * 用户需求（2026-06-26）：估值表从交易面板下方移到侧边栏独立页面。
 */

// 交易类型中文映射
const TRADE_TYPE_LABELS = { buy: '申购', sell: '赎回', dividend: '分红', others: '其他' }

// 数字格式化工具（本地复制，避免跨组件依赖）
const fmtAmount = (v, symbol = '¥') => {
  if (v == null || isNaN(v)) return '—'
  return symbol + Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
const fmtQty = (v) => {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}
const fmtPct = (v) => {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toFixed(2) + '%'
}

export default function ValuationPanel() {
  const [snapshotRange, setSnapshotRange] = useState({ start_date: null, end_date: null })
  const [selectedDate, setSelectedDate] = useState('')
  const [snapshot, setSnapshot] = useState([])
  const [dailyTrades, setDailyTrades] = useState([])
  const [loadingSnapshot, setLoadingSnapshot] = useState(false)

  // ---- 挂载时获取快照日期范围 ----
  useEffect(() => {
    api.getSnapshotRange().then(data => {
      setSnapshotRange(data)
      // 默认选中 end_date（最新日）
      if (data.end_date) {
        setSelectedDate(data.end_date)
      }
    }).catch(() => {})
  }, [])

  // ---- 选中日期变化时加载快照 + 当日交易 ----
  const loadSnapshotForDate = useCallback((asOf) => {
    if (!asOf) return
    setLoadingSnapshot(true)
    Promise.all([
      api.getSnapshot({ as_of: asOf }),
      api.getDailyTrades({ as_of: asOf }),
    ]).then(([snap, trades]) => {
      setSnapshot(snap || [])
      setDailyTrades(trades || [])
    }).catch(() => {
      setSnapshot([])
      setDailyTrades([])
    }).finally(() => setLoadingSnapshot(false))
  }, [])

  useEffect(() => {
    if (selectedDate) loadSnapshotForDate(selectedDate)
  }, [selectedDate, loadSnapshotForDate])

  // ---- 估值表合计 ----
  const totalAmountCny = snapshot.reduce((s, r) => s + (r.amount_cny || 0), 0)

  return (
    <div className="raised" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 12 }}>估值表</div>

      {/* 日期控件 */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
        <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>选择日期：</label>
        <input
          type="date"
          className="ig"
          value={selectedDate}
          min={snapshotRange.start_date || undefined}
          max={snapshotRange.end_date || undefined}
          onChange={e => setSelectedDate(e.target.value)}
          disabled={!snapshotRange.end_date}
          style={{ width: 140 }}
        />
        {!snapshotRange.end_date && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>（尚无快照，请先提交交易）</span>
        )}
      </div>

      {/* KPI 占位（6 张卡片，内容暂不处理 — 用户需求 8） */}
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className="kpi-card"><div className="kpi-label">总资产</div><div className="kpi-value">—</div><div className="kpi-sub">占位</div></div>
        <div className="kpi-card"><div className="kpi-label">穿透股票</div><div className="kpi-value">—</div><div className="kpi-sub">占位</div></div>
        <div className="kpi-card"><div className="kpi-label">组合PE</div><div className="kpi-value">—</div><div className="kpi-sub">占位</div></div>
        <div className="kpi-card"><div className="kpi-label">当日涨幅</div><div className="kpi-value">—</div><div className="kpi-sub">占位</div></div>
        <div className="kpi-card"><div className="kpi-label">Forecast PE</div><div className="kpi-value">—</div><div className="kpi-sub">占位</div></div>
        <div className="kpi-card"><div className="kpi-label">科技占比</div><div className="kpi-value">—</div><div className="kpi-sub">占位</div></div>
      </div>

      {/* 当日交易 */}
      <div style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ fontSize: 13, marginBottom: 6 }}>当日交易</div>
        {loadingSnapshot ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>加载中...</div>
        ) : dailyTrades.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>当日无交易</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 100 }}>代码</th>
                  <th>名称</th>
                  <th style={{ width: 70 }}>类型</th>
                  <th style={{ width: 100 }}>确认份额</th>
                  <th style={{ width: 100 }}>确认金额</th>
                </tr>
              </thead>
              <tbody>
                {dailyTrades.map((t, idx) => (
                  <tr key={idx}>
                    <td>{t.security_code}</td>
                    <td>{t.security_name || '—'}</td>
                    <td>{TRADE_TYPE_LABELS[t.trade_type] || t.trade_type}</td>
                    <td>{fmtQty(t.confirmed_shares)}</td>
                    <td>{fmtAmount(t.confirmed_amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 估值表（持仓快照） */}
      <div>
        <div className="section-title" style={{ fontSize: 13, marginBottom: 6 }}>估值表</div>
        {loadingSnapshot ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>加载中...</div>
        ) : snapshot.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>无持仓快照</div>
        ) : (
          <div className="table-wrap">
            <table className="data-table" style={{ width: '100%' }}>
              <thead>
                <tr>
                  <th style={{ width: 100 }}>代码</th>
                  <th>名称</th>
                  <th style={{ width: 90 }}>数量</th>
                  <th style={{ width: 90 }}>单价·原</th>
                  <th style={{ width: 90 }}>单价·本</th>
                  <th style={{ width: 110 }}>金额·本</th>
                  <th style={{ width: 70 }}>占比</th>
                </tr>
              </thead>
              <tbody>
                {snapshot.map((r, idx) => {
                  const pct = totalAmountCny > 0 ? (r.amount_cny / totalAmountCny * 100) : 0
                  return (
                    <tr key={idx} style={r.is_cash ? { fontStyle: 'italic', color: 'var(--text-secondary)' } : {}}>
                      <td>{r.security_code}</td>
                      <td>{r.security_name || (r.is_cash ? '现金' : '—')}</td>
                      <td>{fmtQty(r.quantity)}</td>
                      <td>{r.price != null ? Number(r.price).toFixed(4) : '—'}</td>
                      <td>{r.price_cny != null ? Number(r.price_cny).toFixed(4) : '—'}</td>
                      <td>{fmtAmount(r.amount_cny)}</td>
                      <td>{fmtPct(pct)}</td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr style={{ fontWeight: 600, borderTop: '2px solid var(--border)' }}>
                  <td colSpan={5}>合计</td>
                  <td>{fmtAmount(totalAmountCny)}</td>
                  <td>100%</td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
