import React, { useState } from 'react'
import StockMasterTab from './StockMasterTab'
import FundMasterTab from './FundMasterTab'
import IndexMasterTab from './IndexMasterTab'
import ClassificationTab from './ClassificationTab'

/**
 * 主数据页 — 4 sub-tab: 股票/基金/指数/分类维度。
 * 沿用 .subtab-bar / .subtab 样式。
 */
export default function MasterDataPanel() {
  const [tab, setTab] = useState('stock')

  return (
    <div style={{ padding: 16 }}>
      <div className="subtab-bar">
        <button className={tab === 'stock' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('stock')}>股票主数据</button>
        <button className={tab === 'fund' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('fund')}>基金主数据</button>
        <button className={tab === 'index' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('index')}>指数主数据</button>
        <button className={tab === 'classification' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('classification')}>分类维度管理</button>
      </div>
      {tab === 'stock' && <StockMasterTab />}
      {tab === 'fund' && <FundMasterTab />}
      {tab === 'index' && <IndexMasterTab />}
      {tab === 'classification' && <ClassificationTab />}
    </div>
  )
}
