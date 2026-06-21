import React, { useEffect, useState } from 'react'
import CompanyCard from './CompanyCard'
import { getAnalystCoreCompanies } from '../api'

export default function CoreCompaniesPage({ bizDate }) {
  const [companies, setCompanies] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!bizDate) return
    setLoading(true)
    getAnalystCoreCompanies(bizDate)
      .then((d) => {
        const list = d?.companies || []
        // 按组合权重降序排列
        list.sort((a, b) => (b.portfolio?.weight_pct || 0) - (a.portfolio?.weight_pct || 0))
        setCompanies(list)
        setErr(null)
      })
      .catch((e) => {
        setErr(e?.message || '加载失败')
        setCompanies([])
      })
      .finally(() => setLoading(false))
  }, [bizDate])

  if (loading) return <div className="empty">加载核心公司…</div>
  if (err) return <div className="empty">加载失败: {err}</div>
  if (!companies.length) return <div className="empty">暂无核心公司研究报告</div>

  return (
    <div className="company-grid">
      {companies.map((c) => (
        <CompanyCard key={c.stock_code} company={c} bizDate={bizDate} />
      ))}
    </div>
  )
}
