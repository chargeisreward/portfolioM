import React, { useState, useEffect } from 'react'
import IndexPdfUploadTab from './IndexPdfUploadTab'
import AnalystReportTab from './AnalystReportTab'
import IndustryChainTab from './IndustryChainTab'
import FinancialUploadTab from './FinancialUploadTab'

/**
 * 内容上传页 — 4 tab：指数 PDF / 股票报告 / 产业链 / 财务数据。
 * 复用现有 .subtab-bar / .subtab 样式。
 *
 * Props:
 *   preSelectIndex: string  从主数据页"缺指数构成"卡片跳转过来时预选的指数代码
 */
export default function ContentUploadPanel({ preSelectIndex }) {
  const [tab, setTab] = useState('indexPdf')

  /** 从主数据页跳转过来时自动切到 indexPdf tab。 */
  useEffect(() => {
    if (preSelectIndex) setTab('indexPdf')
  }, [preSelectIndex])

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
      {tab === 'indexPdf' && <IndexPdfUploadTab preSelectIndex={preSelectIndex} />}
      {tab === 'analyst' && <AnalystReportTab />}
      {tab === 'chain' && <IndustryChainTab />}
      {tab === 'financials' && <FinancialUploadTab />}
    </div>
  )
}
