import React, { useState } from 'react'
import SecurityMasterTab from './SecurityMasterTab'
import FundIndexMapTab from './FundIndexMapTab'
import IndexDrillBaseTab from './IndexDrillBaseTab'

/**
 * 主数据页 — 证券主数据 + 基金-指数映射 + 指数下钻基础数据。
 * 复用现有 .subtab-bar / .subtab 样式实现 tab 切换。
 *
 * Props:
 *   onMissingConstituents: (indexCode: string) => void
 *     缺指数构成卡片点击时触发，由 App.jsx 透传跳转到内容上传页面。
 */
export default function MasterDataPanel({ onMissingConstituents }) {
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
        <button
          className={tab === 'indexDrillBase' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('indexDrillBase')}
        >
          指数下钻基础数据
        </button>
      </div>
      {tab === 'security' ? <SecurityMasterTab />
        : tab === 'fundIndex' ? <FundIndexMapTab />
        : <IndexDrillBaseTab
            onMissingConstituents={onMissingConstituents}
            onMissingIndexMapping={() => setTab('fundIndex')}
          />}
    </div>
  )
}
