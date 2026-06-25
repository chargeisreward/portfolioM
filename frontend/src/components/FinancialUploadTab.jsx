import React, { useState } from 'react'
import { rawApi as api } from '../api'

/**
 * 财务数据上传 tab。
 * 子 tab 切换：Excel 批量 / 单条表单。
 */
export default function FinancialUploadTab() {
  const [subtab, setSubtab] = useState('excel')

  return (
    <div>
      <div className="subtab-bar" style={{ marginBottom: 12 }}>
        <button className={subtab === 'excel' ? 'subtab active' : 'subtab'} onClick={() => setSubtab('excel')}>
          Excel 批量
        </button>
        <button className={subtab === 'single' ? 'subtab active' : 'subtab'} onClick={() => setSubtab('single')}>
          单条表单
        </button>
      </div>
      {subtab === 'excel' ? <ExcelUpload /> : <SingleForm />}
    </div>
  )
}

/** Excel 批量上传。 */
function ExcelUpload() {
  const [market, setMarket] = useState('CN')
  const [asOfDate, setAsOfDate] = useState(new Date().toISOString().slice(0, 10))
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)

  const handleUpload = async () => {
    if (!file) { alert('请选择文件'); return }
    setUploading(true)
    setResult(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('market', market)
      formData.append('as_of_date', asOfDate)
      const res = await api.post('/admin/upload/financials', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
    } catch (e) {
      alert('上传失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="raised" style={{ padding: 16 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="ig" value={market} onChange={e => setMarket(e.target.value)}>
          <option value="CN">A 股</option>
          <option value="HK">港股</option>
        </select>
        <input type="date" className="ig" value={asOfDate} onChange={e => setAsOfDate(e.target.value)} />
        <input type="file" accept=".xlsx,.xls" onChange={e => setFile(e.target.files[0])} />
        <button className="btn-ghost" onClick={handleUpload} disabled={uploading}>
          {uploading ? '上传中...' : '上传'}
        </button>
      </div>
      {result && (
        <div style={{ marginTop: 12 }}>
          <div>状态：{result.status}</div>
          <div>导入：{result.imported} 条</div>
          {result.errors?.length > 0 && (
            <div style={{ color: 'red', marginTop: 4 }}>
              错误：{result.errors.slice(0, 5).join('; ')}
              {result.errors.length > 5 && ` 等 ${result.errors.length} 条`}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** 单条表单。 */
function SingleForm() {
  const [form, setForm] = useState({
    stock_code: '',
    stock_name: '',
    pe_ttm: '',
    pb_mrq: '',
    ps_ttm: '',
    dividend_yield: '',
    market_cap: '',
    as_of_date: new Date().toISOString().slice(0, 10),
  })
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!form.stock_code) { alert('请输入股票代码'); return }
    setSaving(true)
    try {
      // 转换数值字段
      const data = { ...form }
      ;['pe_ttm', 'pb_mrq', 'ps_ttm', 'dividend_yield', 'market_cap'].forEach(k => {
        if (data[k] === '') delete data[k]
        else data[k] = parseFloat(data[k])
      })
      await api.post('/admin/upload/financials/single', data)
      alert('保存成功')
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="raised" style={{ padding: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, maxWidth: 600 }}>
        <label>股票代码 *</label>
        <input className="ig" value={form.stock_code} onChange={e => setForm({ ...form, stock_code: e.target.value })} placeholder="600519.SH" />
        <label>股票名称</label>
        <input className="ig" value={form.stock_name} onChange={e => setForm({ ...form, stock_name: e.target.value })} />
        <label>PE(TTM)</label>
        <input className="ig" type="number" value={form.pe_ttm} onChange={e => setForm({ ...form, pe_ttm: e.target.value })} />
        <label>PB(MRQ)</label>
        <input className="ig" type="number" value={form.pb_mrq} onChange={e => setForm({ ...form, pb_mrq: e.target.value })} />
        <label>PS(TTM)</label>
        <input className="ig" type="number" value={form.ps_ttm} onChange={e => setForm({ ...form, ps_ttm: e.target.value })} />
        <label>股息率</label>
        <input className="ig" type="number" value={form.dividend_yield} onChange={e => setForm({ ...form, dividend_yield: e.target.value })} />
        <label>总市值(亿)</label>
        <input className="ig" type="number" value={form.market_cap} onChange={e => setForm({ ...form, market_cap: e.target.value })} />
        <label>截止日期 *</label>
        <input className="ig" type="date" value={form.as_of_date} onChange={e => setForm({ ...form, as_of_date: e.target.value })} />
      </div>
      <button className="btn-ghost" onClick={handleSave} disabled={saving} style={{ marginTop: 12 }}>
        {saving ? '保存中...' : '保存'}
      </button>
    </div>
  )
}
