import React, { useEffect, useState } from 'react'
import { mdToHtml, injectPriceBadges, highlightNumbers } from '../utils/markdown'
import { getAnalystStockDetail } from '../api'

const fmtNum = (v, d = 2) => (v == null ? '-' : Number(v).toFixed(d))
const fmtInt = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))
const fmtPct = (v, d = 2) => (v == null ? '-' : `${Number(v).toFixed(d)}%`)
const fmtAmount = (v) => (v == null ? '-' : Math.round(v).toLocaleString('en-US'))

const SECTION_LABELS = [
  { key: 'market_focus', label: '一、市场关注' },
  { key: 'core_competence', label: '二、核心竞争力' },
  { key: 'supply_demand', label: '三、供需格局' },
  { key: 'marginal_change', label: '四、边际变化' },
  { key: 'valuation', label: '五、估值' },
  { key: 'risk', label: '六、风险 / Alpha' },
]

function ReportPager({ sections, latestClose, latestCloseDate }) {
  const [idx, setIdx] = useState(0)
  const total = SECTION_LABELS.length

  const currentKey = SECTION_LABELS[idx].key
  const currentLabel = SECTION_LABELS[idx].label
  const text = sections?.[currentKey]

  const goPrev = () => setIdx((i) => (i > 0 ? i - 1 : total - 1))
  const goNext = () => setIdx((i) => (i < total - 1 ? i + 1 : 0))

  let html = mdToHtml(text)
  if (currentKey === 'valuation' && html) {
    html = injectPriceBadges(html, latestClose, latestCloseDate)
  }
  html = highlightNumbers(html)

  return (
    <div className="report-pager">
      <div className="report-pager-header">
        <button className="report-pager-arrow" onClick={goPrev}>◀</button>
        <div className="report-pager-track">
          {SECTION_LABELS.map((s, i) => (
            <button
              key={s.key}
              className={`report-pager-dot ${i === idx ? 'active' : ''}`}
              onClick={() => setIdx(i)}
              title={s.label}
            />
          ))}
        </div>
        <button className="report-pager-arrow" onClick={goNext}>▶</button>
      </div>

      <div className="report-pager-title">{currentLabel}</div>

      {!text ? (
        <div className="empty">本节无内容</div>
      ) : (
        <div className="report-section">
          <div className="report-html" dangerouslySetInnerHTML={{ __html: html }} />
        </div>
      )}
    </div>
  )
}

export default function CompanyCard({ company, bizDate }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [err, setErr] = useState(null)
  const [tab, setTab] = useState('holding')

  const { stock_code, stock_name, portfolio, report_sections } = company

  useEffect(() => {
    if (!expanded || detail || loadingDetail) return
    setLoadingDetail(true)
    getAnalystStockDetail(stock_code, bizDate)
      .then((d) => {
        setDetail(d)
        setErr(null)
      })
      .catch((e) => setErr(e?.message || '加载详情失败'))
      .finally(() => setLoadingDetail(false))
  }, [expanded, detail, loadingDetail, stock_code, bizDate])

  const toggle = () => setExpanded((v) => !v)

  return (
    <div className={`company-card ${expanded ? 'company-card-open' : ''}`}>
      <div className="company-card-header" onClick={toggle}>
        <div className="company-card-title-row">
          <span className="company-code">{stock_code}</span>
          <span className="company-name">{stock_name || '-'}</span>
          <span className="company-toggle">{expanded ? '▼' : '▸'}</span>
        </div>
        <div className="company-card-stats">
          <div className="company-stat-row">
            <div className="company-stat">
              <span className="lbl">组合权重</span>
              <span className="val" style={{ color: '#ffd54f', fontWeight: 600 }}>
                {portfolio ? fmtPct(portfolio.weight_pct) : '未持仓'}
              </span>
            </div>
            <div className="company-stat">
              <span className="lbl">PE</span>
              <span className="val">{fmtNum(portfolio?.pe_ttm_dynamic)}</span>
            </div>
            <div className="company-stat">
              <span className="lbl">PB</span>
              <span className="val">{fmtNum(portfolio?.pb_mrq_dynamic)}</span>
            </div>
          </div>
          <div className="company-stat-row">
            <div className="company-stat">
              <span className="lbl">PS</span>
              <span className="val">{fmtNum(portfolio?.ps_ttm_dynamic)}</span>
            </div>
            <div className="company-stat company-stat-wide">
              <span className="lbl">最新收盘</span>
              <span className="val">
                {portfolio?.latest_close != null
                  ? `${fmtNum(portfolio.latest_close, 2)} (${portfolio.latest_close_date || '-'})`
                  : '-'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="company-detail">
          <div className="company-tab-bar">
            <button
              className={`company-tab ${tab === 'holding' ? 'active' : ''}`}
              onClick={() => setTab('holding')}
            >
              具体持仓
            </button>
            <button
              className={`company-tab ${tab === 'report' ? 'active' : ''}`}
              onClick={() => setTab('report')}
            >
              研究报告
            </button>
          </div>

          {tab === 'holding' ? (
            loadingDetail ? (
              <div className="empty">加载来源分析…</div>
            ) : err ? (
              <div className="empty">{err}</div>
            ) : (
              <div className="detail-card holdings-card">
                {detail?.source_funds?.length > 0 && (
                  <div className="detail-block">
                    <div className="section-title">来源基金（约当数量口径）</div>
                    <table className="data-table source-funds-table">
                      <thead>
                        <tr>
                          <th>基金代码</th>
                          <th>基金名称</th>
                          <th style={{ textAlign: 'right' }}>约当数量</th>
                          <th style={{ textAlign: 'right' }}>金额(CNY)</th>
                          <th style={{ textAlign: 'right' }}>占组合%</th>
                          <th style={{ textAlign: 'right' }}>占基金%</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.source_funds.map((f, i) => (
                          <tr key={i}>
                            <td>{f.fund_code}</td>
                            <td>{f.fund_name}</td>
                            <td style={{ textAlign: 'right', fontFamily: 'GeistMono,monospace' }}>
                              {fmtInt(f.equivalent_shares)}
                            </td>
                            <td style={{ textAlign: 'right', fontFamily: 'GeistMono,monospace' }}>
                              {fmtAmount(f.fund_amount_cny)}
                            </td>
                            <td style={{ textAlign: 'right', fontFamily: 'GeistMono,monospace' }}>
                              {fmtPct(f.ratio_in_portfolio_pct)}
                            </td>
                            <td style={{ textAlign: 'right', fontFamily: 'GeistMono,monospace' }}>
                              {fmtPct(f.ratio_in_fund_pct)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )
          ) : (
            <div className="detail-card report-card">
              <div className="report-ide">
                <div className="report-ide-header">
                  <span className="report-ide-path">
                    {stock_code}_{stock_name || '公司'}_研究.md
                  </span>
                  <span className="report-ide-lang">Markdown</span>
                </div>
                <ReportPager
                  sections={report_sections}
                  latestClose={portfolio?.latest_close}
                  latestCloseDate={portfolio?.latest_close_date}
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
