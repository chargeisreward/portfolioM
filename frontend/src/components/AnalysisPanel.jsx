import React, { useEffect, useState } from 'react'
import * as api from '../api'
import IndustryBreakdownPanel from './IndustryBreakdownPanel'
import FullHoldingTable from './FullHoldingTable'
import PortfolioVsCsi300Card from './PortfolioVsCsi300Card'
import DrillableFundsPage from './DrillableFundsPage'
import DrilledDimensionPanel from './DrilledDimensionPanel'

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
]

export default function AnalysisPanel() {
  const [active, setActive] = useState('drill')
  const [bizDate, setBizDate] = useState(null)
  const [totalEstCNY, setTotalEstCNY] = useState(0)

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

      {/* Dimension body */}
      {active === 'drill' ? (
        <div className="raised" style={{ padding: 12, marginTop: 10 }}>
          <DrillableFundsPage bizDate={bizDate} />
        </div>
      ) : active === 'full' ? (
        <div className="raised" style={{ padding: 12, marginTop: 10 }}>
          <PortfolioVsCsi300Card bizDate={bizDate} totalEstCNY={totalEstCNY} />
          <FullHoldingTable bizDate={bizDate} onTotalEstChange={setTotalEstCNY} />
        </div>
      ) : (
        <div className="raised" style={{ padding: 12, marginTop: 10 }}>
          {(() => {
            const cfg = DIMS.find(d => d.id === active)
            const dim = cfg?.dim || active
            const market = cfg?.market || 'A+H'
            const DRILLED_DIMS = ['swy1', 'swy2', 'swy3', 'csi1', 'csi2', 'csi3', 'csi4', 'se1']
            if (DRILLED_DIMS.includes(dim)) {
              return <DrilledDimensionPanel dim={dim} bizDate={bizDate} market={market} label={cfg?.label} />
            }
            return <IndustryBreakdownPanel dim={dim} market={market} bizDate={bizDate} />
          })()}
        </div>
      )}
    </div>
  )
}