import React, { useEffect, useState } from 'react'
import { getDataVersion } from '../api'

const fmtNum = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d))

/**
 * PortfolioVsCsi300Card — header KPI strip comparing 4 scopes:
 *  A+H  (全股票)
 *  A    (A股)
 *  H    (港股)
 *  CSI300 (经股价调整后的权重)
 *
 * 计算口径：虚拟盈利法 (Σ amount/PE) / Σ amount
 * PE = total / Σ(amount/PE), PB/PS 同理
 * 股息率 = Σ(amount × dy) / total  (直接加权)
 *
 * 点击任一卡片 → 展开中间计算表（按 amount/PE 贡献最大的前 30 只股票）。
 * 再次点击 → 折叠。
 */
export default function PortfolioVsCsi300Card({ bizDate }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(true)
  const [expandedKey, setExpandedKey] = useState(null)

  useEffect(() => {
    if (!bizDate) return
    setLoading(true)
    import('../api').then(api => {
      api.getPortfolioVsCsi300(bizDate)
        .then(d => { setData(d); setLoading(false); })
        .catch(e => { setErr(e?.message || 'load failed'); setLoading(false); })
    })
  }, [bizDate])

  if (err) return <div className="scope-card scope-error">加载 4 口径对比失败: {err}</div>
  if (loading) return <div className="scope-card">加载 4 口径对比…</div>
  if (!data) return null

  const SCOPES = [
    { key: 'ah', label: 'A+H (全股票)', data: data.ah },
    { key: 'a', label: 'A 股', data: data.a_only },
    { key: 'h', label: '港股', data: data.h_only },
    { key: 'csi300', label: 'CSI 300', data: data.csi300 },
  ]

  const toggle = (key) => setExpandedKey(prev => prev === key ? null : key)

  return (
    <div className="scope-card">
      <div className="scope-header">
        <span className="scope-title">4 口径估值对比 (虚拟盈利法 · 5/29 基准 · 点击展开计算过程)</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          PE/PB/PS = total / Σ(amount/x)  ·  股息率 = Σ(amount·dy)/total
        </span>
      </div>
      <div className="scope-grid">
        {SCOPES.map(s => {
          const isOpen = expandedKey === s.key
          return (
            <div key={s.key} className={`scope-cell ${isOpen ? 'scope-cell-open' : ''}`}>
              <div className="scope-label" onClick={() => toggle(s.key)} style={{ cursor: 'pointer' }}>
                {s.label} {isOpen ? '▼' : '▸'}
              </div>
              <div className="scope-stats">
                <div className="scope-stat">
                  <span className="scope-stat-label">PE</span>
                  <span className="scope-stat-value">{fmtNum(s.data?.weighted_pe)}</span>
                </div>
                <div className="scope-stat">
                  <span className="scope-stat-label">PB</span>
                  <span className="scope-stat-value">{fmtNum(s.data?.weighted_pb)}</span>
                </div>
                <div className="scope-stat">
                  <span className="scope-stat-label">PS</span>
                  <span className="scope-stat-value">{fmtNum(s.data?.weighted_ps)}</span>
                </div>
                <div className="scope-stat">
                  <span className="scope-stat-label">股息率%</span>
                  <span className="scope-stat-value">{fmtNum(s.data?.weighted_dividend_yield)}</span>
                </div>
                <div className="scope-stat scope-meta">
                  <span className="scope-stat-label">只数</span>
                  <span className="scope-stat-value">{s.data?.stock_count ?? '-'}</span>
                </div>
                <div className="scope-stat scope-meta">
                  <span className="scope-stat-label">{s.key === 'csi300' ? '权重%' : '金额(CNY)'}</span>
                  <span className="scope-stat-value" style={{ fontSize: 11 }}>
                    {s.key === 'csi300'
                      ? fmtNum(s.data?.total_amount_cny, 2)
                      : (s.data?.total_amount_cny ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </span>
                </div>
              </div>
              {isOpen && (
                <div className="scope-detail">
                  <div className="scope-detail-header">
                    <span>{s.label} 计算明细 — top 30 贡献股票 (按 amount/PE 降序)</span>
                    <button className="btn-ghost" onClick={() => toggle(s.key)} style={{ fontSize: 11 }}>折叠</button>
                  </div>
                  <table className="data-table" style={{ fontSize: 11 }}>
                    <thead>
                      <tr>
                        <th>代码</th>
                        <th style={{ textAlign: 'right' }}>amount</th>
                        <th style={{ textAlign: 'right' }}>PE</th>
                        <th style={{ textAlign: 'right' }}>amount/PE</th>
                        <th style={{ textAlign: 'right' }}>贡献%</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(s.data?.top_pe_contributors || []).map((row, idx) => (
                        <tr key={idx}>
                          <td>{row.stock}</td>
                          <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                            {row.amount.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                          </td>
                          <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                            {row.pe?.toFixed(2)}
                          </td>
                          <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                            {row.amt_pe.toLocaleString('en-US', { maximumFractionDigits: 2 })}
                          </td>
                          <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                            {((row.amt_pe / (s.data.virtual_earnings || 1)) * 100).toFixed(1)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot>
                      <tr style={{ fontWeight: 600, borderTop: '1px solid var(--border-strong)' }}>
                        <td colSpan={3}>合计（前30 + 其余）</td>
                        <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                          {fmtNum(s.data?.virtual_earnings, 0)} 虚拟盈利
                        </td>
                        <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>
                          PE = {(s.data?.total_amount_cny || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })} / {fmtNum(s.data?.virtual_earnings, 0)} = <b>{fmtNum(s.data?.weighted_pe)}</b>
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}