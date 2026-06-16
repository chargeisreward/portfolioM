import React, { useEffect, useState } from 'react'
import * as api from '../api'

/**
 * API 策略页面：列出所有数据源 + 限制 + 覆盖范围。
 * 数据源：api_strategies.json (manifest) + 实时扫描 backend 代码 (live hook)
 */
export default function StrategiesPanel() {
  const [manifest, setManifest] = useState({ strategies: [] })
  const [live, setLive] = useState({ sources: [], total: 0, scanned_at: '' })
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getStrategies()
      .then(d => {
        setManifest(d.manifest || { strategies: [] })
        setLive(d.live || { sources: [], total: 0, scanned_at: '' })
      })
      .catch(e => setError('加载失败：' + (e?.message || e)))
  }, [])

  const fileFunc = (s) => `${s.module.split('/').pop()} :: ${s.function}`

  return (
    <div>
      {error && (
        <div className="raised" style={{ borderColor: 'var(--down)', color: 'var(--down)' }}>
          {error}
        </div>
      )}

      <div className="raised">
        <div className="section-title">数据源策略 · 清单</div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          数据源：<code style={{ fontFamily: '"GeistMono", monospace' }}>backend/api_strategies.json</code> ·
          维护者：开发者编辑此文件后 push git 即可生效
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>名称</th>
              <th>类型</th>
              <th>数据</th>
              <th>覆盖</th>
              <th>限流</th>
            </tr>
          </thead>
          <tbody>
            {manifest.strategies.map(s => (
              <tr key={s.id}>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11 }}>{s.id}</td>
                <td>
                  <div style={{ fontWeight: 600 }}>{s.name}</div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace' }}>
                    {fileFunc(s)}
                  </div>
                </td>
                <td><span className="cur-btn on" style={{ fontSize: 10 }}>{s.type}</span></td>
                <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{s.data_type}</td>
                <td style={{ fontSize: 11 }}>
                  {s.covers?.map(c => (
                    <span key={c} style={{
                      display: 'inline-block', padding: '1px 6px', margin: '0 2px 2px 0',
                      border: '1px solid var(--border)', fontSize: 10,
                      fontFamily: '"GeistMono", monospace',
                    }}>{c}</span>
                  ))}
                </td>
                <td style={{ fontSize: 11, color: 'var(--text-muted)' }}>{s.rate_limit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="raised">
        <div className="section-title">代码实时扫描（Live Hook）</div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          后端启动 + 每次访问此页面时，扫描 <code style={{ fontFamily: '"GeistMono", monospace' }}>crawlers/</code> + <code style={{ fontFamily: '"GeistMono", monospace' }}>services/</code> 中所有 fetch_/crawl_ 函数。
          最后扫描：<code style={{ fontFamily: '"GeistMono", monospace' }}>{live.scanned_at}</code> · 共 {live.total} 个
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>函数</th>
              <th>文件</th>
              <th>行</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {live.sources.map((s, i) => (
              <tr key={`${s.file}-${s.function}-${i}`}>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, color: 'var(--text)' }}>
                  {s.function}
                </td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                  {s.file}
                </td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                  {s.line}
                </td>
                <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  {s.doc || <span style={{ color: 'var(--text-muted)' }}>—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="raised" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
        <div className="section-title">说明</div>
        <ul style={{ lineHeight: 1.7, paddingLeft: 20 }}>
          <li>所有数据获取均通过公开 API（无需 Token），无商业授权风险</li>
          <li>实时行情每 15 分钟轮询（scheduler.realtime_prices）</li>
          <li>历史价完整性每日凌晨 5:00 补缺（scheduler.backfill_gaps）</li>
          <li>汇率每日更新（scheduler.industry_crawler_data 任务 4）</li>
          <li>不使用任何 mock/种子数据 — 所有数据均为真实拉取</li>
        </ul>
      </div>
    </div>
  )
}
