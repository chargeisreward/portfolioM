import React, { useEffect, useState } from 'react'
import * as api from '../api'

/**
 * API 策略页面：列出所有数据源 + 限制 + 覆盖范围 + 代码映射表 +
 * 调度任务实时状态 + 数据新鲜度 + 数据预览。
 *
 * 数据源：api_strategies.json (manifest) + 实时扫描 backend 代码 (live hook) +
 *        /api/scheduler/status + /api/data-freshness + /api/data-preview
 */
export default function StrategiesPanel() {
  const [manifest, setManifest] = useState({ strategies: [] })
  const [live, setLive] = useState({ sources: [], total: 0, scanned_at: '' })
  const [codeMaps, setCodeMaps] = useState([])
  const [filterApi, setFilterApi] = useState('')
  const [error, setError] = useState(null)

  // ---- 调度任务实时状态 ----
  const [sched, setSched] = useState({ running: false, jobs: [] })
  const [triggerMsg, setTriggerMsg] = useState(null)
  // ---- 数据新鲜度 ----
  const [freshness, setFreshness] = useState({ as_of: '', tables: [] })
  // ---- 数据预览 ----
  const [previewTable, setPreviewTable] = useState('price_cache')
  const [previewStock, setPreviewStock] = useState('')
  const [previewLimit, setPreviewLimit] = useState(20)
  const [preview, setPreview] = useState({ table: '', rows: [], total_rows: 0 })
  const [previewErr, setPreviewErr] = useState(null)
  // ---- 代码映射覆盖率（pre-flight）----
  const [coverage, setCoverage] = useState(null)
  const [coveragePool, setCoveragePool] = useState('all')
  const [coverageErr, setCoverageErr] = useState(null)

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

  // 调度状态 10s 轮询
  useEffect(() => {
    let mounted = true
    const tick = () => api.getSchedulerStatus().then(d => { if (mounted) setSched(d) }).catch(() => {})
    tick()
    const t = setInterval(tick, 10000)
    return () => { mounted = false; clearInterval(t) }
  }, [])

  // 数据新鲜度 60s 轮询
  useEffect(() => {
    let mounted = true
    const tick = () => api.getDataFreshness().then(d => { if (mounted) setFreshness(d) }).catch(() => {})
    tick()
    const t = setInterval(tick, 60000)
    return () => { mounted = false; clearInterval(t) }
  }, [])

  // 数据预览：表 / 过滤条件变化时拉取
  useEffect(() => {
    let mounted = true
    setPreviewErr(null)
    api.getDataPreview(previewTable, { limit: previewLimit, stock_code: previewStock || undefined })
      .then(d => { if (mounted) setPreview(d) })
      .catch(e => { if (mounted) setPreviewErr('预览加载失败：' + (e?.message || e)) })
    return () => { mounted = false }
  }, [previewTable, previewStock, previewLimit])

  // 代码映射覆盖率：手动 / 池变化时拉取（不轮询 — 大查询）
  const loadCoverage = (pool) => {
    setCoverageErr(null)
    api.getCodeMapCoverage(pool || 'all')
      .then(d => setCoverage(d))
      .catch(e => setCoverageErr('覆盖率加载失败：' + (e?.message || e)))
  }
  useEffect(() => { loadCoverage(coveragePool) }, [coveragePool])

  const triggerJob = (jobId) => {
    setTriggerMsg(`已排队 ${jobId}，请稍候 5-10s 后刷新查看 last_run_at`)
    api.triggerSchedulerJob(jobId, false, true)
      .then(d => setTriggerMsg(`✓ ${jobId} ${d.mode || 'ok'}`))
      .catch(e => setTriggerMsg(`✗ ${jobId}: ${e?.message || e}`))
    setTimeout(() => setTriggerMsg(null), 8000)
  }

  // 健康度判断（绿/黄/红/灰）
  const health = (t) => {
    if (!t.max_date) return { label: '无数据', color: 'var(--text-muted)', dot: '⚫' }
    if (t.max_date < freshness.as_of) return { label: '过期', color: 'var(--down)', dot: '🔴' }
    if (t.max_created_at) {
      const ageMin = (Date.now() - new Date(t.max_created_at).getTime()) / 60000
      if (ageMin <= 30) return { label: '健康', color: 'var(--up)', dot: '🟢' }
      return { label: `滞后 ${Math.round(ageMin)}min`, color: '#f59e0b', dot: '🟡' }
    }
    return { label: '待写入', color: 'var(--text-muted)', dot: '⚫' }
  }

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
              <th style={{ minWidth: 96 }}>类型</th>
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
                <td style={{ minWidth: 96 }}><span className="cur-btn on" style={{ fontSize: 10 }}>{s.type}</span></td>
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

      {/* ========== 代码映射覆盖率（定时拉取前预检） ========== */}
      <div className="raised">
        <div className="section-title">
          代码映射覆盖率 ·
          {coverage ? (
            coverage.health === 'ok'
              ? <span style={{ color: 'var(--up)' }}> ✓ 全部映射完成</span>
              : <span style={{ color: 'var(--down)' }}> ⚠ {coverage.total_missing} 个未映射</span>
          ) : ' 加载中…'}
        </div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          三个证券池（持仓 / 关注 / 已下钻）× 五个候选 API 策略的代码映射完整性检查。
          定时拉取任务（realtime_prices / fill_snapshot_gaps_smart / backfill_gaps）开始前会先跑这个检查，发现缺失记录到 _JOB_LAST_RUN.last_result。
          数据源：<code style={{ fontFamily: '"GeistMono", monospace' }}>GET /api/code-map/coverage</code>
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--text-muted)', marginRight: 4 }}>池</span>
          {['all', 'holdings', 'watchlist', 'drilled'].map(p => (
            <button key={p} onClick={() => setCoveragePool(p)}
              className={coveragePool === p ? 'cur-btn on' : 'cur-btn'} style={{ fontSize: 10 }}>{p}</button>
          ))}
          <button onClick={() => loadCoverage(coveragePool)}
            className="cur-btn" style={{ fontSize: 10, marginLeft: 8 }}>↻ 重新检查</button>
          <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 8 }}>
            上次检查：{coverage ? (coverage.checked_at || '—') : '—'}
          </span>
        </div>
        {coverageErr && (
          <div style={{ fontSize: 11, color: 'var(--down)', marginBottom: 8 }}>{coverageErr}</div>
        )}
        <table className="data-table">
          <thead>
            <tr>
              <th>池</th>
              <th style={{ textAlign: 'right' }}>证券数</th>
              <th style={{ textAlign: 'right' }}>映射条目</th>
              <th style={{ textAlign: 'right' }}>已映射</th>
              <th style={{ textAlign: 'right' }}>未映射</th>
              <th>未映射示例</th>
            </tr>
          </thead>
          <tbody>
            {(coverage?.pools || []).map(p => (
              <tr key={p.name} style={p.missing > 0 ? { borderLeft: '2px solid var(--down)' } : { borderLeft: '2px solid var(--up)' }}>
                <td style={{ fontSize: 11, fontWeight: 600 }}>{p.name}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, textAlign: 'right' }}>{p.total_codes}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, textAlign: 'right', color: 'var(--text-muted)' }}>{p.rows_count}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, textAlign: 'right', color: 'var(--up)' }}>{p.mapped}</td>
                <td style={{
                  fontFamily: '"GeistMono", monospace', fontSize: 11, textAlign: 'right',
                  color: p.missing > 0 ? 'var(--down)' : 'var(--text-muted)',
                  fontWeight: p.missing > 0 ? 600 : 400,
                }}>{p.missing}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-secondary)' }}>
                  {p.missing_examples.length === 0
                    ? <span style={{ color: 'var(--text-muted)' }}>—</span>
                    : p.missing_examples.map((ex, i) => (
                        <span key={i} style={{
                          display: 'inline-block', padding: '1px 5px', margin: '0 3px 2px 0',
                          border: '1px solid var(--down)', color: 'var(--down)',
                          borderRadius: 3,
                        }}>
                          {ex.code} → {ex.api}
                        </span>
                      ))}
                  {p.rows_truncated && (
                    <span style={{ marginLeft: 6, color: 'var(--text-muted)' }}>(rows 截断显示前 500 行)</span>
                  )}
                </td>
              </tr>
            ))}
            {!coverage && (
              <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 11, padding: 12 }}>加载中…</td></tr>
            )}
          </tbody>
        </table>
        {coverage && coverage.total_missing > 0 && (
          <div style={{ fontSize: 10, color: 'var(--down)', marginTop: 8, fontFamily: '"GeistMono", monospace' }}>
            ⚠ 修复方法：在 <code>backend/services/code_map.py</code> 的 _default_transform 加规则，或调用
            <code style={{ marginLeft: 4 }}>POST /api/code-map</code> 手动补映射。
          </div>
        )}
      </div>

      {/* ========== 调度任务实时状态 ========== */}
      <div className="raised">
        <div className="section-title">
          调度任务实时状态 · {sched.running ? `${sched.jobs.length} jobs running` : '⚠ scheduler not running'}
        </div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          数据源：<code style={{ fontFamily: '"GeistMono", monospace' }}>GET /api/scheduler/status</code> ·
          轮询间隔 10s ·
          如果 next_run 为空或 last_run_at 为空 → 调度器可能未运行或该 job 已停。
          「立即触发」按钮调用 <code style={{ fontFamily: '"GeistMono", monospace' }}>POST /api/scheduler/trigger/&lt;job_id&gt;</code>。
        </div>
        {triggerMsg && (
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, fontFamily: '"GeistMono", monospace' }}>
            {triggerMsg}
          </div>
        )}
        <table className="data-table">
          <thead>
            <tr>
              <th>Job ID</th>
              <th>名称</th>
              <th>下次执行</th>
              <th>最近执行</th>
              <th>状态</th>
              <th>耗时</th>
              <th>错误 / 结果</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {sched.jobs.map(j => (
              <tr key={j.id} style={j.last_status === 'error' ? { borderLeft: '2px solid var(--down)' } : null}>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11 }}>{j.id}</td>
                <td style={{ fontSize: 11 }}>{j.name}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>{j.next_run || '—'}</td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>{j.last_run_at || '—'}</td>
                <td>
                  {j.last_status === 'ok' && <span style={{ color: 'var(--up)', fontSize: 11 }}>✓ ok</span>}
                  {j.last_status === 'error' && <span style={{ color: 'var(--down)', fontSize: 11 }}>✗ error</span>}
                  {!j.last_status && <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>—</span>}
                </td>
                <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                  {j.last_duration_ms != null ? `${j.last_duration_ms}ms` : '—'}
                </td>
                <td style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"GeistMono", monospace', maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {j.last_error ? <span style={{ color: 'var(--down)' }}>{j.last_error}</span> :
                   j.last_result ? <span style={{ color: 'var(--text-secondary)' }}>{JSON.stringify(j.last_result).slice(0, 80)}</span> :
                   '—'}
                </td>
                <td>
                  <button onClick={() => triggerJob(j.id)} className="cur-btn" style={{ fontSize: 10 }}>立即触发</button>
                </td>
              </tr>
            ))}
            {!sched.running && (
              <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--down)', fontSize: 11, padding: 12 }}>
                调度器未运行。检查 backend startup() 是否被调用 / 容器是否在重启中。
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ========== 数据新鲜度 ========== */}
      <div className="raised">
        <div className="section-title">
          数据新鲜度 · as_of {freshness.as_of || '(加载中)'}
        </div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          数据源：<code style={{ fontFamily: '"GeistMono", monospace' }}>GET /api/data-freshness</code> ·
          轮询间隔 60s ·
          健康度 = (max_date == 今天 && 最近落库 &lt; 30min) → 🟢 健康；max_date 过期 → 🔴 过期
        </div>
        <table className="data-table">
          <thead>
            <tr>
              <th>表</th>
              <th>最新业务日期</th>
              <th>最后落库时间</th>
              <th>今日写入</th>
              <th>健康度</th>
            </tr>
          </thead>
          <tbody>
            {freshness.tables.map(t => {
              const h = health(t)
              return (
                <tr key={t.table}>
                  <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11 }}>{t.table}</td>
                  <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11 }}>{t.max_date || '—'}</td>
                  <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 10, color: 'var(--text-muted)' }}>
                    {t.max_created_at ? t.max_created_at.replace('T', ' ').slice(0, 19) : '—'}
                  </td>
                  <td style={{ fontFamily: '"GeistMono", monospace', fontSize: 11, textAlign: 'right' }}>{t.rows_today ?? '—'}</td>
                  <td><span style={{ color: h.color, fontSize: 11 }}>{h.dot} {h.label}</span></td>
                </tr>
              )
            })}
            {freshness.tables.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 11, padding: 12 }}>
                加载中…
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ========== 数据预览 ========== */}
      <div className="raised">
        <div className="section-title">数据预览 · 最近 {previewLimit} 行</div>
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8 }}>
          数据源：<code style={{ fontFamily: '"GeistMono", monospace' }}>GET /api/data-preview?table=...</code> ·
          用于直观检查数据是否落库 — 看最新一行 trade_date / current_price_date 是否为今天
        </div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>表：</label>
          <select value={previewTable} onChange={e => setPreviewTable(e.target.value)}
            style={{ fontSize: 11, padding: '2px 6px', background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border)' }}>
            <option value="price_cache">price_cache</option>
            <option value="a_share_snapshot">a_share_snapshot</option>
            <option value="hk_share_snapshot">hk_share_snapshot</option>
            <option value="holding">holding</option>
          </select>
          <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>股票代码：</label>
          <input value={previewStock} onChange={e => setPreviewStock(e.target.value)}
            placeholder="(空=全部)"
            style={{ fontSize: 11, padding: '2px 6px', background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border)', fontFamily: '"GeistMono", monospace', width: 140 }} />
          <label style={{ fontSize: 11, color: 'var(--text-muted)' }}>行数：</label>
          <select value={previewLimit} onChange={e => setPreviewLimit(Number(e.target.value))}
            style={{ fontSize: 11, padding: '2px 6px', background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border)' }}>
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
          <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 'auto' }}>
            表总行数：{preview.total_rows.toLocaleString()}
          </span>
        </div>
        {previewErr && (
          <div style={{ fontSize: 11, color: 'var(--down)', marginBottom: 8 }}>{previewErr}</div>
        )}
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                {preview.rows[0] ? Object.keys(preview.rows[0]).map(k => <th key={k}>{k}</th>) : <th>—</th>}
              </tr>
            </thead>
            <tbody>
              {preview.rows.map((r, i) => (
                <tr key={i}>
                  {Object.entries(r).map(([k, v]) => (
                    <td key={k} style={{
                      fontFamily: '"GeistMono", monospace', fontSize: 10,
                      color: ['current_price', 'close_px', 'price'].includes(k) ? 'var(--chart-up)' : 'var(--text-secondary)',
                      whiteSpace: 'nowrap',
                    }}>{v ?? '—'}</td>
                  ))}
                </tr>
              ))}
              {preview.rows.length === 0 && (
                <tr><td style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 11, padding: 12 }}>
                  无数据
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
