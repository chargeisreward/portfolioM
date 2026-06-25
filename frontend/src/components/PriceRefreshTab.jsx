import React, { useState } from 'react'
import { adminFillPricesAll, adminRefreshAnalysisPrices } from '../api'

/**
 * 价格刷新 tab — 管理员手动触发公共数据层的增量价格刷新。
 *
 * 两个区块：
 *  1. 持仓最新价刷新：全用户持仓并集去重 → 15min TTL 增量刷新公共缓存 → 回填所有 user 的 Holding 行
 *  2. 分析页收盘价刷新：指定业务日的下钻股票（NULL 填充）+ 未下钻基金（净值补缺）
 *
 * 设计原则：公共数据层模式（一次刷新全员受益），增量（TTL 命中跳过 / NULL 才填），
 * 分批触发（max_codes 避免单次超时，remaining_null>0 时再次触发）。
 */
export default function PriceRefreshTab() {
  // 卡片1：持仓最新价刷新
  const [loading1, setLoading1] = useState(false)
  const [result1, setResult1] = useState(null)
  const [err1, setErr1] = useState('')

  // 卡片2：分析页收盘价刷新
  const today = new Date().toISOString().slice(0, 10)
  const [asOf, setAsOf] = useState(today)
  const [maxCodes, setMaxCodes] = useState(200)
  const [loading2, setLoading2] = useState(false)
  const [result2, setResult2] = useState(null)
  const [err2, setErr2] = useState('')

  /** 触发持仓最新价刷新（全用户并集 + TTL 增量）。 */
  const runFillPricesAll = async () => {
    setLoading1(true); setErr1(''); setResult1(null)
    try {
      const r = await adminFillPricesAll()
      setResult1(r)
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || '请求失败'
      setErr1(msg)
    } finally {
      setLoading1(false)
    }
  }

  /** 触发分析页收盘价刷新（下钻股票 NULL 填充 + 基金净值补缺）。 */
  const runRefreshAnalysis = async () => {
    if (!asOf) { setErr2('请选择业务日期'); return }
    setLoading2(true); setErr2(''); setResult2(null)
    try {
      const r = await adminRefreshAnalysisPrices(asOf, 5, maxCodes)
      setResult2(r)
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || '请求失败'
      setErr2(msg)
    } finally {
      setLoading2(false)
    }
  }

  return (
    <div>
      {/* ===== 卡片1：持仓最新价刷新 ===== */}
      <div className="raised" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>持仓最新价刷新</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              全用户持仓并集去重 → 15min TTL 增量刷新公共缓存 → 回填所有 user 的 Holding 行
            </div>
          </div>
          <button
            className="btn-ghost"
            onClick={runFillPricesAll}
            disabled={loading1}
            style={{ minWidth: 120 }}
          >
            {loading1 ? '刷新中...' : '触发刷新'}
          </button>
        </div>

        {err1 && (
          <div style={{ color: 'var(--down)', fontSize: 12, marginTop: 8 }}>{err1}</div>
        )}

        {result1 && (
          <ResultGrid>
            <ResultItem label="唯一证券数" value={result1.unique_codes} />
            <ResultItem label="缓存命中（跳过）" value={result1.cache_hit} tone="muted" />
            <ResultItem label="缓存刷新（调 API）" value={result1.cache_refreshed} tone="up" />
            <ResultItem label="缓存失败" value={result1.cache_miss} tone={result1.cache_miss > 0 ? 'down' : 'muted'} />
            <ResultItem label="回填 Holding 行数" value={result1.holdings_updated} tone="up" />
            <ResultItem label="总 Holding 行数" value={result1.total_holdings} tone="muted" />
          </ResultGrid>
        )}
      </div>

      {/* ===== 卡片2：分析页收盘价刷新 ===== */}
      <div className="raised" style={{ padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>分析页收盘价刷新</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              指定业务日的下钻股票（NULL 填充）+ 未下钻基金（净值补缺 5 天）。增量：只填 NULL，不覆盖已有值。
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            业务日期
            <input
              type="date"
              className="ig"
              value={asOf}
              onChange={e => setAsOf(e.target.value)}
              style={{ marginLeft: 6 }}
            />
          </label>
          <label style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            单次最大股票数
            <input
              type="number"
              className="ig"
              min="1"
              max="5000"
              value={maxCodes}
              onChange={e => setMaxCodes(Number(e.target.value) || 200)}
              style={{ marginLeft: 6, width: 90 }}
            />
          </label>
          <button
            className="btn-ghost"
            onClick={runRefreshAnalysis}
            disabled={loading2}
            style={{ minWidth: 120 }}
          >
            {loading2 ? '刷新中...' : '触发刷新'}
          </button>
        </div>

        {err2 && (
          <div style={{ color: 'var(--down)', fontSize: 12, marginTop: 8 }}>{err2}</div>
        )}

        {result2 && (
          <div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>
              业务日期：{result2.as_of_date}
            </div>

            <div style={{ fontSize: 13, fontWeight: 600, marginTop: 8, marginBottom: 4 }}>下钻股票（NULL 填充）</div>
            <ResultGrid>
              <ResultItem label="尝试填充行数" value={result2.stocks?.attempted} />
              <ResultItem label="成功填充行数" value={result2.stocks?.fetched} tone="up" />
              <ResultItem label="跳过（无数据）" value={result2.stocks?.skipped_no_data} tone="muted" />
              <ResultItem label="失败" value={result2.stocks?.failed} tone={result2.stocks?.failed > 0 ? 'down' : 'muted'} />
            </ResultGrid>

            <div style={{ fontSize: 13, fontWeight: 600, marginTop: 12, marginBottom: 4 }}>未下钻基金（净值补缺）</div>
            <ResultGrid>
              <ResultItem label="尝试基金数" value={result2.funds?.attempted} />
              <ResultItem label="净值行写入数" value={result2.funds?.nav_rows_written} tone="up" />
              <ResultItem label="跳过（无数据）" value={result2.funds?.skipped_no_data} tone="muted" />
            </ResultGrid>

            <div style={{ fontSize: 13, fontWeight: 600, marginTop: 12, marginBottom: 4 }}>剩余 NULL（需再次触发）</div>
            <ResultGrid>
              <ResultItem label="A 股 NULL" value={result2.remaining_null?.a_share} tone={result2.remaining_null?.a_share > 0 ? 'down' : 'muted'} />
              <ResultItem label="港股 NULL" value={result2.remaining_null?.hk} tone={result2.remaining_null?.hk > 0 ? 'down' : 'muted'} />
              <ResultItem label="合计 NULL" value={result2.remaining_null?.total} tone={result2.remaining_null?.total > 0 ? 'down' : 'up'} />
            </ResultGrid>
            {result2.remaining_null?.total > 0 && (
              <div style={{ fontSize: 12, color: 'var(--down)', marginTop: 6 }}>
                ⚠️ 仍有 {result2.remaining_null.total} 行 NULL，再次点击"触发刷新"继续分批填充（每次最多 {maxCodes} 只）。
              </div>
            )}
            {result2.remaining_null?.total === 0 && (
              <div style={{ fontSize: 12, color: 'var(--up)', marginTop: 6 }}>
                ✅ 全部填充完成。
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

/** 三列网格的结果展示区，复用 .data-table 风格但更紧凑。 */
function ResultGrid({ children }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
      gap: 8,
      marginTop: 8,
    }}>
      {children}
    </div>
  )
}

/** 单个结果项：label + value，value 根据 tone 着色。 */
function ResultItem({ label, value, tone = 'default' }) {
  const color = tone === 'up' ? 'var(--up)'
    : tone === 'down' ? 'var(--down)'
    : tone === 'muted' ? 'var(--text-muted)'
    : 'var(--text)'
  return (
    <div style={{
      padding: '8px 10px',
      background: 'var(--bg)',
      borderRadius: 4,
      border: '1px solid var(--border)',
    }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color, marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
        {value ?? '-'}
      </div>
    </div>
  )
}
