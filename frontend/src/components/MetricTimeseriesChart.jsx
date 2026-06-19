import React, { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { getTimeseries } from '../api'

/**
 * MetricTimeseriesChart — click-to-expand trend chart (spec §4.6).
 * connectNulls: false so missing dates stay as gaps (no forward-fill).
 */
export default function MetricTimeseriesChart({ metric = 'pe_weighted', window = 90, scope = 'both' }) {
  const ref = useRef(null)
  const instRef = useRef(null)
  const [state, setState] = React.useState({ data: [], missing: [], loading: true })

  useEffect(() => {
    let cancelled = false
    setState(s => ({ ...s, loading: true }))
    getTimeseries(scope, metric, window)
      .then(d => {
        if (cancelled) return
        setState({ data: d.data || [], missing: d.missing_dates || [], loading: false })
      })
      .catch(() => !cancelled && setState(s => ({ ...s, loading: false })))
    return () => { cancelled = true }
  }, [metric, window, scope])

  useEffect(() => {
    if (!ref.current || state.loading) return
    if (!instRef.current || !instRef.current.dom) {
      // Dispose any orphan first
      const existing = echarts.getInstanceByDom(ref.current)
      if (existing) existing.dispose()
      instRef.current = echarts.init(ref.current)
    }
    const c = instRef.current
    const byDate = {}
    for (const row of state.data) {
      (byDate[row.calc_date] = byDate[row.calc_date] || {})[row.scope] = row.value
    }
    const dates = Object.keys(byDate).sort()
    const portfolioLine = dates.map(d => [d, byDate[d].portfolio ?? null])
    const csi300Line = dates.map(d => [d, byDate[d].csi300 ?? null])
    c.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['组合', 'CSI300'], top: 0 },
      grid: { top: 30, right: 16, bottom: 30, left: 56 },
      xAxis: { type: 'time' },
      yAxis: { type: 'value', scale: true },
      series: [
        { name: '组合', type: 'line', data: portfolioLine, connectNulls: false, smooth: false, showSymbol: false },
        { name: 'CSI300', type: 'line', data: csi300Line, connectNulls: false, smooth: false, showSymbol: false },
      ],
    }, true)
    // No cleanup — instance persists for component lifetime
  }, [state, scope])

  // Dispose on unmount only
  useEffect(() => () => {
    if (instRef.current) {
      instRef.current.dispose()
      instRef.current = null
    }
  }, [])

  if (state.loading) return <div className="timeseries-loading">加载时序数据…</div>
  if (!state.data.length) return <div className="timeseries-empty">时序数据为空（业务日期切换后等待下次导入）</div>
  const subtitle = state.missing.length
    ? `缺 ${state.missing.length} 个交易日（不补）`
    : '连续'
  return (
    <div className="timeseries-container">
      <div ref={ref} style={{ width: '100%', height: 220 }} />
      <div className="timeseries-subtitle">{subtitle}</div>
    </div>
  )
}