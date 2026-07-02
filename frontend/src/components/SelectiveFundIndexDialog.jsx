import React, { useState, useEffect, useCallback } from 'react'
import * as api from '../api'

/**
 * 基金-指数映射 双向选择弹窗。
 * Step 1: 选基金 (模糊搜索 fund_master)
 * Step 2: 选指数 (模糊搜索 index_master)
 * Step 3: 填业绩基准
 * 确认 → POST /admin/fund-index-map/selective
 */
export default function SelectiveFundIndexDialog({ open, onClose, onSuccess }) {
  const [step, setStep] = useState(1)
  const [fundSearch, setFundSearch] = useState('')
  const [fundResults, setFundResults] = useState([])
  const [selectedFund, setSelectedFund] = useState(null)

  const [idxSearch, setIdxSearch] = useState('')
  const [idxResults, setIdxResults] = useState([])
  const [selectedIndex, setSelectedIndex] = useState(null)

  const [benchmark, setBenchmark] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState(null)

  const searchFunds = useCallback(async (q) => {
    if (!q) { setFundResults([]); return }
    try {
      const res = await api.fundMasterLookup(q)
      setFundResults(res.items || [])
    } catch (e) { console.error(e) }
  }, [])
  const searchIndices = useCallback(async (q) => {
    if (!q) { setIdxResults([]); return }
    try {
      const res = await api.indexMasterLookup(q)
      setIdxResults(res.items || [])
    } catch (e) { console.error(e) }
  }, [])

  useEffect(() => {
    if (!open) {
      setStep(1); setSelectedFund(null); setSelectedIndex(null)
      setBenchmark(''); setErr(null); setFundSearch(''); setIdxSearch('')
    }
  }, [open])

  if (!open) return null

  const handleSubmit = async () => {
    setSaving(true); setErr(null)
    try {
      await api.fundIndexMapSelective({
        fund_code: selectedFund.fund_code,
        index_code: selectedIndex.index_code,
        benchmark_formula: benchmark || undefined,
        as_of_date: new Date().toISOString().slice(0, 10),
      })
      onSuccess?.()
      onClose()
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}
           style={{ maxWidth: 600, width: '90%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <h3>新增基金-指数映射</h3>
          <button className="btn-ghost" onClick={onClose}>×</button>
        </div>

        <div style={{ marginTop: 12 }}>
          <h4>1. 选择基金 {selectedFund && <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>({selectedFund.fund_code})</span>}</h4>
          <input className="ig" placeholder="搜索代码或名称"
                 value={fundSearch}
                 onChange={(e) => { setFundSearch(e.target.value); searchFunds(e.target.value) }} />
          {fundResults.map(r => (
            <div key={r.fund_code} className="raised" style={{ padding: 8, marginTop: 4, cursor: 'pointer' }}
                 onClick={() => { setSelectedFund(r); setStep(2) }}>
              {r.fund_code}  {r.fund_name}
            </div>
          ))}
        </div>

        <div style={{ marginTop: 16 }}>
          <h4>2. 选择指数 {selectedIndex && <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>({selectedIndex.index_code})</span>}</h4>
          <input className="ig" placeholder="搜索代码或名称"
                 value={idxSearch}
                 onChange={(e) => { setIdxSearch(e.target.value); searchIndices(e.target.value) }} />
          {idxResults.map(r => (
            <div key={r.index_code} className="raised" style={{ padding: 8, marginTop: 4, cursor: 'pointer' }}
                 onClick={() => { setSelectedIndex(r); setStep(3) }}>
              {r.index_code}  {r.index_name}
            </div>
          ))}
        </div>

        <div style={{ marginTop: 16 }}>
          <h4>3. 业绩比较基准（可选）</h4>
          <input className="ig" style={{ width: '100%' }}
                 value={benchmark} onChange={(e) => setBenchmark(e.target.value)}
                 placeholder="沪深300指数收益率×95% + 银行活期×5%" />
        </div>

        {err && <div style={{ color: 'red', marginTop: 12 }}>{err}</div>}

        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <button className="btn-ghost" onClick={onClose}>取消</button>
          <button className="btn-ghost" style={{ marginLeft: 8 }}
                  disabled={!selectedFund || !selectedIndex || saving}
                  onClick={handleSubmit}>
            {saving ? '保存中…' : '确认新增'}
          </button>
        </div>
      </div>
    </div>
  )
}