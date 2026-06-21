import React, { useState } from 'react'
import { mdToHtml, highlightNumbers } from '../utils/markdown'

const fmtNum = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d))
const fmtPct = (v, d = 2) => (v == null ? '-' : `${Number(v).toFixed(d)}%`)
const fmtAmount = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))

const SORTED_HOLDINGS_CACHE = new WeakMap()
function sortCompaniesByRelevance(list) {
  if (!list || list.length === 0) return list
  if (SORTED_HOLDINGS_CACHE.has(list)) return SORTED_HOLDINGS_CACHE.get(list)
  const sorted = [...list].sort((a, b) => {
    const ra = a.relevance_stars || 0
    const rb = b.relevance_stars || 0
    if (rb !== ra) return rb - ra
    return (b.portfolio_weight_pct || 0) - (a.portfolio_weight_pct || 0)
  })
  SORTED_HOLDINGS_CACHE.set(list, sorted)
  return sorted
}

function ComparisonRows({ portfolioMetrics, csi300Metrics, showNote }) {
  const pm = portfolioMetrics || {}
  const cm = csi300Metrics || {}
  const rows = [
    { label: '权重', asset: fmtPct(pm.weight_pct), bench: fmtPct(cm.weight_pct) },
    { label: '规模(CNY)', asset: fmtAmount(pm.amount_cny), bench: '-' },
    { label: 'PE(加权)', asset: fmtNum(pm.pe_weighted), bench: fmtNum(cm.pe_weighted) },
    { label: 'PB(加权)', asset: fmtNum(pm.pb_weighted), bench: fmtNum(cm.pb_weighted) },
    { label: 'PS(加权)', asset: fmtNum(pm.ps_weighted), bench: fmtNum(cm.ps_weighted) },
    { label: '股票数', asset: pm.stock_count ?? '-', bench: cm.stock_count ?? '-' },
  ]

  return (
    <div>
      {showNote && (
        <div className="chain-metrics-note">
          资产权重 = 产业链内下钻持仓合计金额 / 全部下钻证券合计金额；300指数权重 = 产业链内沪深300成分股原始指数权重之和
        </div>
      )}
      <div className="chain-metrics-grid">
        <div className="chain-metrics-header">
          <span>指标</span>
          <span>资产</span>
          <span>300指数</span>
        </div>
        {rows.map((r, i) => (
          <div key={i} className="chain-metrics-row">
            <span className="chain-metrics-label">{r.label}</span>
            <span className="chain-metrics-value">{r.asset}</span>
            <span className="chain-metrics-value">{r.bench}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ChainCard({ chain }) {
  const [expanded, setExpanded] = useState(false)
  const [tab, setTab] = useState('portfolio')

  const toggle = () => setExpanded((v) => !v)

  const {
    chain_name,
    narrative_md,
    company_count,
    companies_in_portfolio,
    portfolio_metrics,
    csi300_metrics,
  } = chain

  return (
    <div className={`chain-card ${expanded ? 'chain-card-open' : ''}`}>
      <div className="chain-card-header" onClick={toggle}>
        <div className="chain-card-title-row">
          <span className="chain-name">{chain_name}</span>
          <span className="chain-count">
            持仓公司
            <span style={{ color: 'var(--accent)', fontWeight: 600, marginLeft: 4 }}>
              {company_count}
            </span>
          </span>
          <span className="chain-toggle">{expanded ? '▼' : '▸'}</span>
        </div>

        {!expanded && (
          <ComparisonRows
            portfolioMetrics={portfolio_metrics}
            csi300Metrics={csi300_metrics}
            showNote
          />
        )}
      </div>

      {expanded && (
        <div className="chain-detail">
          <div className="company-tab-bar">
            <button
              className={`company-tab ${tab === 'portfolio' ? 'active' : ''}`}
              onClick={() => setTab('portfolio')}
            >
              组合
            </button>
            <button
              className={`company-tab ${tab === 'report' ? 'active' : ''}`}
              onClick={() => setTab('report')}
            >
              研究报告
            </button>
          </div>

          {tab === 'portfolio' ? (
            <div className="detail-card">
              <div className="detail-block">
                <div className="section-title">持仓公司</div>
                {companies_in_portfolio.length === 0 ? (
                  <div className="empty">当前 portfolio 未持有该产业链中的公司</div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>产业链位置</th>
                        <th>细分环节</th>
                        <th>公司简称</th>
                        <th>证券代码</th>
                        <th style={{ textAlign: 'center' }}>相关程度</th>
                        <th style={{ textAlign: 'right' }}>组合权重%</th>
                        <th style={{ textAlign: 'right' }}>金额(CNY)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortCompaniesByRelevance(companies_in_portfolio).map((c, i) => (
                        <tr key={i}>
                          <td>{c.chain_position}</td>
                          <td>{c.sub_segment || '-'}</td>
                          <td>{c.company_name}</td>
                          <td>{c.stock_code}</td>
                          <td style={{ textAlign: 'center', color: 'var(--relevance-orange)', letterSpacing: '1px' }}>
                            <span style={{ color: '#ff8c1a' }}>{'★'.repeat(c.relevance_stars || 0)}</span>
                            <span style={{ color: 'var(--text-muted)' }}>{'☆'.repeat(5 - (c.relevance_stars || 0))}</span>
                          </td>
                          <td
                            style={{
                              textAlign: 'right',
                              fontFamily: 'GeistMono,monospace',
                            }}
                          >
                            {fmtPct(c.portfolio_weight_pct)}
                          </td>
                          <td
                            style={{
                              textAlign: 'right',
                              fontFamily: 'GeistMono,monospace',
                            }}
                          >
                            {fmtAmount(c.amount_cny)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          ) : (
            <div className="detail-card report-card">
              <div className="report-ide">
                <div className="report-ide-header">
                  <span className="report-ide-path">
                    {chain_name}_研究报告.md
                  </span>
                  <span className="report-ide-lang">Markdown</span>
                </div>
                <div
                  className="report-html"
                  dangerouslySetInnerHTML={{
                    __html: highlightNumbers(mdToHtml(narrative_md)),
                  }}
                />
              </div>
            </div>
          )}

          <div style={{ marginTop: 8, textAlign: 'right' }}>
            <button className="btn-ghost" onClick={toggle} style={{ fontSize: 11 }}>
              折叠
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
