import React, { useState } from 'react'
import IndexPdfUploadTab from './IndexPdfUploadTab'
import AnalystReportTab from './AnalystReportTab'
import IndustryChainTab from './IndustryChainTab'
import FinancialUploadTab from './FinancialUploadTab'

/**
 * 内容上传页 — 4 tab：指数 PDF / 股票报告 / 产业链 / 财务数据。
 * 复用现有 .subtab-bar / .subtab 样式。
 */
export default function ContentUploadPanel() {
  const [tab, setTab] = useState('indexPdf')

  return (
    <div style={{ padding: 16 }}>
      <div className="subtab-bar">
        <button
          className={tab === 'indexPdf' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('indexPdf')}
        >
          指数构成 PDF
        </button>
        <button
          className={tab === 'analyst' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('analyst')}
        >
          股票分析报告
        </button>
        <button
          className={tab === 'chain' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('chain')}
        >
          产业链报告
        </button>
        <button
          className={tab === 'financials' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('financials')}
        >
          财务数据
        </button>
      </div>
      {tab === 'indexPdf' && <IndexPdfUploadTab />}
      {tab === 'analyst' && <AnalystReportTab />}
      {tab === 'chain' && <IndustryChainTab />}
      {tab === 'financials' && <FinancialUploadTab />}
    </div>
  )
}
