import React, { useEffect, useState } from 'react'
import * as api from '../api'
import IndustryBreakdownPanel from './IndustryBreakdownPanel'
import MetricTimeseriesChart from './MetricTimeseriesChart'
import FullHoldingTable from './FullHoldingTable'
import PortfolioVsCsi300Card from './PortfolioVsCsi300Card'
import DrillableFundsPage from './DrillableFundsPage'

const DIMS = [
  { id: 'drill', label: '下钻', special: 'drill' },
  { id: 'full', label: '全持仓' },
  { id: 'swy1', label: '申万L1' },
  { id: 'swy2', label: '申万L2' },
  { id: 'swy3', label: '申万L3' },
  { id: 'csi1', label: '中证L1' },
  { id: 'csi2', label: '中证L2' },
  { id: 'csi3', label: '中证L3' },
  { id: 'csi4', label: '中证L4' },
  { id: 'a_strategic_emerging', label: 'A股战略新兴', dim: 'se1', market: 'A' },
  { id: 'hk_concept', label: '港股概念', dim: 'se1', market: 'H' },
  { id: 'chain', label: '产业链' },
  { id: 'growth_tier', label: '增长分层' },
  { id: 'competition', label: '竞争格局' },
  { id: 'valuation', label: '估值时序' },
]

export default function AnalysisPanel() {
  const [active, setActive] = useState('drill')
  const [bizDate, setBizDate] = useState(null)

  useEffect(() => {
    api.getDataVersion().then(d => setBizDate(d?.current_business_date)).catch(() => {})
  }, [])

  return (
    <div>
      {/* Tabs */}
      <div className="raised" style={{ padding: '6px 12px', marginBottom: 10 }}>
        <div style={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          {DIMS.map(d => (
            <button key={d.id} onClick={() => setActive(d.id)}
              style={{
                padding: '5px 12px', border: 'none', borderRadius: 6,
                background: active === d.id ? 'var(--accent)' : 'transparent',
                color: active === d.id ? '#fff' : 'var(--text-secondary)',
                cursor: 'pointer', fontSize: 12, fontWeight: active === d.id ? 600 : 400,
                transition: 'all 0.15s',
              }}>{d.label}</button>
          ))}
        </div>
      </div>

      {/* 4-scope summary card — always visible above */}
      <PortfolioVsCsi300Card bizDate={bizDate} />

      {/* Dimension body */}
      {active === 'drill' ? (
        <div className="raised" style={{ padding: 12, marginTop: 10 }}>
          <DrillableFundsPage bizDate={bizDate} />
        </div>
      ) : active === 'full' ? (
        <div className="raised" style={{ padding: 12, marginTop: 10 }}>
          <FullHoldingTable bizDate={bizDate} />
        </div>
      ) : active === 'valuation' ? (
        <div className="raised" style={{ padding: 12, marginTop: 10 }}>
          <div className="section-title">估值时序（90/180/360 天）</div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {['pe_weighted', 'pb_weighted', 'ps_weighted'].map(m => (
              <div key={m} style={{ flex: '1 1 320px', minWidth: 320 }}>
                <div className="metric-label">{m.replace('_weighted', '').toUpperCase()}</div>
                <MetricTimeseriesChart metric={m} window={90} scope="both" />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="raised" style={{ padding: 12, marginTop: 10 }}>
          {(() => {
            const cfg = DIMS.find(d => d.id === active)
            const dim = cfg?.dim || active
            const market = cfg?.market || 'A+H'
            return <IndustryBreakdownPanel dim={dim} market={market} bizDate={bizDate} />
          })()}
        </div>
      )}
    </div>
  )
}