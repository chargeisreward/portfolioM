import React, { useState } from 'react'
import DataReadinessTab from './DataReadinessTab'
import TaskHistoryTab from './TaskHistoryTab'
import JobExecutionsTab from './JobExecutionsTab'
import ApiStrategyTab from './ApiStrategyTab'
import TradingCalendarView from './TradingCalendarView'
import DataBrowser from './DataBrowser'
import PriceRefreshTab from './PriceRefreshTab'

/**
 * 数据源页 — 数据就绪 + 任务历史 + 执行监控 + API策略 + 交易日历 + 数据浏览 + 价格刷新。
 * 复用现有 .subtab-bar / .subtab 样式实现 tab 切换（与 MasterDataPanel 一致）。
 */
export default function DataSourcePanel() {
  const [tab, setTab] = useState('readiness')

  const tabs = [
    { id: 'readiness', label: '数据就绪' },
    { id: 'tasks', label: '任务历史' },
    { id: 'executions', label: '执行监控' },
    { id: 'apiStrategy', label: 'API策略' },
    { id: 'calendar', label: '交易日历' },
    { id: 'browser', label: '数据浏览' },
    { id: 'priceRefresh', label: '价格刷新' },
  ]

  return (
    <div style={{ padding: 16 }}>
      <div className="subtab-bar">
        {tabs.map(t => (
          <button
            key={t.id}
            className={tab === t.id ? 'subtab active' : 'subtab'}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'readiness' && <DataReadinessTab />}
      {tab === 'tasks' && <TaskHistoryTab />}
      {tab === 'executions' && <JobExecutionsTab />}
      {tab === 'apiStrategy' && <ApiStrategyTab />}
      {tab === 'calendar' && <TradingCalendarView />}
      {tab === 'browser' && <DataBrowser />}
      {tab === 'priceRefresh' && <PriceRefreshTab />}
    </div>
  )
}
