import React, { useEffect, useState } from 'react'
import CoreCompaniesPage from './CoreCompaniesPage'
import IndustryChainsPage from './IndustryChainsPage'
import { getDataVersion } from '../api'

const SUBTABS = [
  { id: 'core', label: '核心公司' },
  { id: 'chain', label: '产业链' },
]

export default function AnalystPanel() {
  const [activeTab, setActiveTab] = useState('core')
  const [bizDate, setBizDate] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getDataVersion()
      .then((d) => setBizDate(d?.current_business_date || null))
      .catch(() => setBizDate(null))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="analyst-panel" style={{ width: '100%' }}>
      <div className="subtab-bar" style={{ marginBottom: 12 }}>
        {SUBTABS.map((t) => (
          <button
            key={t.id}
            className={`subtab ${activeTab === t.id ? 'active' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="empty">加载业务日期…</div>
      ) : !bizDate ? (
        <div className="empty">业务日期未就绪，请先在「设置」刷新数据</div>
      ) : activeTab === 'core' ? (
        <CoreCompaniesPage bizDate={bizDate} />
      ) : (
        <IndustryChainsPage bizDate={bizDate} />
      )}
    </div>
  )
}
