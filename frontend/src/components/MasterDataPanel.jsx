import React, { useState } from 'react'
import SecurityMasterTab from './SecurityMasterTab'
import FundIndexMapTab from './FundIndexMapTab'

/**
 * 主数据页 — 证券主数据 + 基金-指数映射。
 * 复用现有 .subtab-bar / .subtab 样式实现 tab 切换。
 */
export default function MasterDataPanel() {
  const [tab, setTab] = useState('security')

  return (
    <div style={{ padding: 16 }}>
      <div className="subtab-bar">
        <button
          className={tab === 'security' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('security')}
        >
          证券主数据
        </button>
        <button
          className={tab === 'fundIndex' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('fundIndex')}
        >
          基金-指数映射
        </button>
      </div>
      {tab === 'security' ? <SecurityMasterTab /> : <FundIndexMapTab />}
    </div>
  )
}
