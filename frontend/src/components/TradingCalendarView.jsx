import React, { useState, useEffect } from 'react'
import * as api from '../api'

const MARKETS = [
  { id: 'CN', label: 'CN 沪深' },
  { id: 'HK', label: 'HK 港股' },
  { id: 'US', label: 'US 美股' },
  { id: 'OF', label: 'OF 场外' },
]
const WD = ['一', '二', '三', '四', '五', '六', '日']
const MONTH_NAMES = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']

function pad2(n) { return n < 10 ? '0' + n : '' + n }
function isoOf(year, month, day) { return `${year}-${pad2(month)}-${pad2(day)}` }

export default function TradingCalendarView() {
  const now = new Date()
  const [market, setMarket] = useState('CN')
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [data, setData] = useState({ cells: [], summary: { trading: 0, holiday: 0, weekend: 0 } })
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getCalendarMonth(market, year, month)
      .then(d => { setData(d); setError(null) })
      .catch(e => setError('加载失败：' + (e?.message || e)))
  }, [market, year, month])

  const goPrev = () => {
    if (month === 1) { setYear(y => y - 1); setMonth(12) } else { setMonth(m => m - 1) }
  }
  const goNext = () => {
    if (month === 12) { setYear(y => y + 1); setMonth(1) } else { setMonth(m => m + 1) }
  }
  const goToday = () => {
    const t = new Date()
    setYear(t.getFullYear()); setMonth(t.getMonth() + 1)
  }
  const todayIso = isoOf(now.getFullYear(), now.getMonth() + 1, now.getDate())

  return (
    <div>
      <div className="section-title">📅 交易日历</div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        {MARKETS.map(m => (
          <button key={m.id}
            onClick={() => setMarket(m.id)}
            className={market === m.id ? 'cur-btn on' : 'cur-btn'}
            style={{ fontSize: 11 }}>
            {m.label}
          </button>
        ))}
        <span style={{ width: 12 }} />
        <button onClick={goPrev} className="btn-ghost" style={{ padding: '2px 8px' }}>‹</button>
        <span style={{ fontFamily: '"GeistMono", monospace', fontSize: 13, fontWeight: 600, minWidth: 100, textAlign: 'center' }}>
          {year} · {MONTH_NAMES[month - 1]}
        </span>
        <button onClick={goNext} className="btn-ghost" style={{ padding: '2px 8px' }}>›</button>
        <button onClick={goToday} className="btn-ghost" style={{ padding: '2px 8px', fontSize: 11 }}>今天</button>
      </div>

      {error && (
        <div className="raised" style={{ borderColor: 'var(--down)', color: 'var(--down)', marginBottom: 8, fontSize: 11 }}>
          {error}
        </div>
      )}

      {/* 周表头 */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4, marginBottom: 4,
      }}>
        {WD.map(w => (
          <div key={w} style={{
            textAlign: 'center', fontSize: 10, color: 'var(--text-muted)',
            fontFamily: '"GeistMono", monospace', padding: 4,
          }}>{w}</div>
        ))}
      </div>

      {/* 日历格 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4 }}>
        {data.cells.map((c, i) => {
          const day = parseInt(c.date.slice(8, 10), 10)
          const isToday = c.date === todayIso
          let bg = 'transparent'
          let color = 'var(--text-muted)'
          if (c.in_month) {
            const dow = new Date(c.date + 'T00:00:00').getDay()  // 0=Sun
            if (dow === 0 || dow === 6) {
              bg = 'rgba(255,255,255,0.03)'
              color = 'var(--text-muted)'
            } else if (c.is_trading) {
              bg = 'rgba(74,184,122,0.18)'  // 绿
              color = 'var(--text)'
            } else {
              bg = 'rgba(228,90,90,0.18)'   // 红
              color = 'var(--text)'
            }
          }
          return (
            <div key={c.date + '-' + i} title={c.note ? `${c.date} · ${c.note}` : c.date} style={{
              position: 'relative',
              padding: '8px 4px',
              minHeight: 44,
              background: bg,
              border: isToday ? '1.5px solid var(--accent-primary)' : '1px solid var(--border)',
              borderRadius: 4,
              textAlign: 'center',
              fontSize: 12,
              fontFamily: '"GeistMono", monospace',
              color,
              opacity: c.in_month ? 1 : 0.4,
              cursor: c.note ? 'help' : 'default',
            }}>
              {c.in_month ? day : ''}
              {c.in_month && !c.is_trading && c.note && (
                <div style={{
                  fontSize: 8, color: 'var(--text-muted)', marginTop: 2,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{c.note}</div>
              )}
            </div>
          )
        })}
      </div>

      {/* 汇总 */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginTop: 12,
      }}>
        <div style={{ padding: '6px 10px', background: 'rgba(74,184,122,0.12)', borderRadius: 4, textAlign: 'center' }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>交易日</div>
          <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--chart-up)' }}>{data.summary.trading}</div>
        </div>
        <div style={{ padding: '6px 10px', background: 'rgba(228,90,90,0.12)', borderRadius: 4, textAlign: 'center' }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>节假日</div>
          <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--chart-down)' }}>{data.summary.holiday}</div>
        </div>
        <div style={{ padding: '6px 10px', background: 'rgba(255,255,255,0.04)', borderRadius: 4, textAlign: 'center' }}>
          <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>周末</div>
          <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-secondary)' }}>{data.summary.weekend}</div>
        </div>
      </div>
    </div>
  )
}
