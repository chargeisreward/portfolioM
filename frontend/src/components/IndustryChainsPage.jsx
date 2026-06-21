import React, { useEffect, useState } from 'react'
import ChainCard from './ChainCard'
import { getAnalystIndustryChains } from '../api'

export default function IndustryChainsPage({ bizDate }) {
  const [chains, setChains] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!bizDate) return
    setLoading(true)
    getAnalystIndustryChains(bizDate)
      .then((d) => {
        setChains(d?.chains || [])
        setErr(null)
      })
      .catch((e) => {
        setErr(e?.message || '加载失败')
        setChains([])
      })
      .finally(() => setLoading(false))
  }, [bizDate])

  if (loading) return <div className="empty">加载产业链…</div>
  if (err) return <div className="empty">加载失败: {err}</div>
  if (!chains.length) return <div className="empty">暂无产业链分析</div>

  return (
    <div className="chain-grid">
      {chains.map((c) => (
        <ChainCard key={c.chain_name} chain={c} />
      ))}
    </div>
  )
}
