import React from 'react'

/**
 * IndustryDrilldownTable — detail rows for one dimension bucket.
 * Renders the per-stock list returned by /api/penetration/dimension-detail.
 */
export default function IndustryDrilldownTable({ dim, keyName, detail }) {
  if (!detail) return <div className="detail-loading">下钻明细加载中…</div>
  const stocks = detail.stocks || []
  if (stocks.length === 0) return <div className="detail-empty">本维度无明细</div>

  return (
    <div className="industry-drilldown" style={{ padding: 12 }}>
      <div style={{ marginBottom: 6, fontSize: 12, color: 'var(--text-muted)' }}>
        {dim}={keyName} · {stocks.length} 只
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th style={{ textAlign: 'right' }}>金额(CNY)</th>
            <th style={{ textAlign: 'right' }}>PE</th>
            <th style={{ textAlign: 'right' }}>PB</th>
            <th style={{ textAlign: 'right' }}>PS</th>
            <th>二级行业</th>
            <th>链位置</th>
            <th>来源基金</th>
            <th style={{ textAlign: 'center' }}>直持</th>
          </tr>
        </thead>
        <tbody>
          {stocks.map(s => (
            <tr key={s.stock_code}>
              <td title={s.stock_code} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.stock_code}
              </td>
              <td title={s.stock_name} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.stock_name || '-'}
              </td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace', fontWeight: 600 }}>
                {s.amount_cny?.toLocaleString() || '-'}
              </td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{s.pe_ttm_dynamic?.toFixed(2) ?? '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{s.pb_mrq_dynamic?.toFixed(2) ?? '-'}</td>
              <td style={{ textAlign: 'right', fontFamily: '"GeistMono",monospace' }}>{s.ps_ttm_dynamic?.toFixed(2) ?? '-'}</td>
              <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{s.industry_l2 || '-'}</td>
              <td style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{s.chain_position || '-'}</td>
              <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{(s.source_funds || []).join(', ') || '-'}</td>
              <td style={{ textAlign: 'center', fontSize: 11, color: 'var(--text-secondary)' }}>{s.is_direct ? '是' : '否'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}