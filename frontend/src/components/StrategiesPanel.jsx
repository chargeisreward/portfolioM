import React, { useEffect, useState } from 'react'
import * as api from '../api'

/**
 * API 策略页面：列出所有数据源 + 限制 + 覆盖范围 + 代码映射表。
 * 数据源：api_strategies.json (manifest) + 实时扫描 backend 代码 (live hook) + api_code_map 表
 */
export default function StrategiesPanel() {
  const [manifest, setManifest] = useState({ strategies: [] })
  const [live, setLive] = useState({ sources: [], total: 0, scanned_at: '' })
  const [codeMaps, setCodeMaps] = useState([])
  const [filterApi, setFilterApi] = useState('')
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getStrategies()
      .then(d => {
        setManifest(d.manifest || { strategies: [] })
        setLive(d.live || { sources: [], total: 0, scanned_at: '' })
      })
      .catch(e => setError('加载失败：' + (e?.message || e)))
  }, [])

  useEffect(() => {
    api.getCodeMaps(filterApi || undefined)
      .then(d => setCodeMaps(d.items || []))
      .catch(() => setCodeMaps([]))
  }, [filterApi])

  const fileFunc = (s) => `${s.module.split('/').pop()} :: ${s.function}`

  // distinct api_strategy values for filter
  const distinctApis = [...new Set(codeMaps.map(m => m.api_strategy))].sort()

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

      {/* 代码映射表 */}
      <div className="raised">
        <div className="section-title">代码映射表 · {codeMaps.length} 条</div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          同一个证券在不同的 API 有不同的代码格式。本表统一标准代码 → 各 API 调用代码的转换。
          所有 fetch 函数入口处先查本表。来源：<code style={{ fontFamily: '"GeistMono", monospace' }}>backend/api_code_map</code> 表 + 内置默认规则惰性持久化。
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace', marginRight: 4 }}>API</span>
          <button onClick={() => setFilterApi('')}
            className={filterApi === '' ? 'cur-btn on' : 'cur-btn'} style={{ fontSize: 10 }}>全部</button>
          {distinctApis.map(a => (
            <button key={a} onClick={() => setFilterApi(a)}
              className={filterApi === a ? 'cur-btn on' : 'cur-btn'} style={{ fontSize: 10 }}>{a}</button>
          ))}
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>标准代码</th>
              <th>API 策略</th>
              <th>调用代码</th>
              <th>市场</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {codeMaps.map((m, i) => (
              <tr key={`${m.code_in}-${m.api_strategy}-${i}`}>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11 }}>{m.code_in}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--accent-primary)' }}>{m.api_strategy}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, color: 'var(--chart-up)' }}>{m.code_out}</td>
                <td style={{ fontSize: 10, color: 'var(--text-muted)' }}>{m.market || '-'}</td>
                <td style={{ fontSize: 10, color: 'var(--text-secondary)' }}>{m.note || '-'}</td>
              </tr>
            ))}
            {codeMaps.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 11, padding: 12 }}>
                暂无映射（empty）
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
