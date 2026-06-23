import React, { useState } from 'react'
import {
  postImport, postCrawlAll, postPenetration, postRecalcCsi300,
  postFillPrices, triggerSchedulerJob,
} from '../api'

/**
 * 运维面板 — 仅管理员可见
 * 接管原「总览页刷新」按钮的 5 个手动操作 + scheduler job 触发
 */
const Button = ({ label, action, color, busy }) => (
  <button
    onClick={action}
    disabled={busy}
    style={{
      padding: '12px 16px', margin: 8, border: '1px solid var(--border)',
      borderRadius: 6, background: color || 'var(--bg)', color: 'var(--text)',
      cursor: busy ? 'wait' : 'pointer', minWidth: 180, opacity: busy ? 0.6 : 1,
    }}
  >{label}</button>
)

const SCHED_JOBS = [
  { id: 'realtime_prices', label: '实时价格刷新' },
  { id: 'financial_fundamentals', label: '财务基本面更新' },
  { id: 'industry_crawler', label: '行业爬虫' },
  { id: 'backfill_gaps', label: '回填缺口' },
  { id: 'fill_snapshot_gaps_smart', label: '智能补缺' },
  { id: 'detect_data_gaps', label: '数据补足检测' },
  { id: 'crawl_global_news', label: '全球新闻' },
  { id: 'crawl_stock_news', label: '个股新闻' },
  { id: 'crawl_announcements_research', label: '公告研报' },
]

export default function OpsPanel() {
  const [busy, setBusy] = useState(null)
  const [result, setResult] = useState(null)

  async function run(key, label, fn) {
    if (!confirm(`确认执行「${label}」？`)) return
    setBusy(key)
    setResult(null)
    try {
      const r = await fn()
      setResult({ key, ok: true, data: r })
    } catch (e) {
      setResult({ key, ok: false, error: e?.response?.data?.detail || e.message })
    }
    setBusy(null)
  }

  return (
    <div style={{ padding: 24 }}>
      <h2>运维</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: 12 }}>
        仅管理员可见。执行数据维护操作。
      </p>

      <div style={{ display: 'flex', flexWrap: 'wrap' }}>
        <Button label="导入持仓 Excel" busy={busy === 'import'} action={() => run('import', '导入持仓', postImport)} />
        <Button label="抓取价格/全量" busy={busy === 'crawl'} action={() => run('crawl', '全量抓取', postCrawlAll)} />
        <Button label="执行下钻" busy={busy === 'pen'} action={() => run('pen', '下钻', postPenetration)} />
        <Button label="重算 CSI300" busy={busy === 'csi300'} action={() => run('csi300', 'CSI300 重算', postRecalcCsi300)} />
        <Button label="补齐价格" busy={busy === 'fill'} action={() => run('fill', '补价', postFillPrices)} />
      </div>

      <h3 style={{ marginTop: 24, fontSize: 14 }}>调度任务触发</h3>
      <div style={{ display: 'flex', flexWrap: 'wrap' }}>
        {SCHED_JOBS.map(j => (
          <Button
            key={j.id}
            label={`触发 ${j.label}`}
            busy={busy === `job-${j.id}`}
            color="var(--bg-raised, #f4f4f5)"
            action={() => run(`job-${j.id}`, j.label, () => triggerSchedulerJob(j.id, false, true))}
          />
        ))}
      </div>

      {result && (
        <div style={{
          marginTop: 16, padding: 12, border: '1px solid var(--border)', borderRadius: 6,
          background: result.ok ? 'rgba(34,197,94,0.05)' : 'rgba(220,38,38,0.05)',
        }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            {result.ok ? '✅' : '❌'} {result.key}
          </div>
          <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {JSON.stringify(result.ok ? result.data : result.error, null, 2).slice(0, 500)}
          </pre>
        </div>
      )}
    </div>
  )
}
