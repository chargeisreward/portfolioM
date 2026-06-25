import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

/**
 * 指数构成 PDF 上传 tab。
 * 选择指数 → 上传 PDF → 预览解析结果 → 确认写入。
 */
export default function IndexPdfUploadTab() {
  const [indexList, setIndexList] = useState([])
  const [selectedIndex, setSelectedIndex] = useState('')
  const [asOfDate, setAsOfDate] = useState(new Date().toISOString().slice(0, 10))
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [preview, setPreview] = useState(null)
  const [taskId, setTaskId] = useState(null)
  const [error, setError] = useState('')

  /** 加载可下钻基金关联的指数列表。 */
  const loadIndexList = useCallback(async () => {
    try {
      const res = await api.get('/admin/security-master', { params: { drillable: true, page_size: 200 } })
      const items = res.data.items || []
      // 提取 index_code/index_name 去重
      const indexMap = new Map()
      items.forEach(item => {
        if (item.index_code) {
          indexMap.set(item.index_code, item.index_name || item.index_code)
        }
      })
      setIndexList(Array.from(indexMap.entries()).map(([code, name]) => ({ code, name })))
    } catch (e) {
      console.error('加载指数列表失败', e)
    }
  }, [])

  useEffect(() => { loadIndexList() }, [loadIndexList])

  /** 上传 PDF。 */
  const handleUpload = async () => {
    if (!selectedIndex) { alert('请选择指数'); return }
    if (!file) { alert('请选择 PDF 文件'); return }
    setUploading(true)
    setError('')
    setPreview(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('index_code', selectedIndex)
      formData.append('as_of_date', asOfDate)
      const res = await api.post('/admin/upload/index-pdf', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      if (res.data.status === 'success') {
        setPreview(res.data.preview)
        setTaskId(res.data.task_id)
      } else {
        setError(res.data.error || '解析失败')
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setUploading(false)
    }
  }

  /** 确认写入。 */
  const handleConfirm = async () => {
    if (!taskId) return
    try {
      const res = await api.post('/admin/upload/index-pdf/confirm', { task_id: taskId })
      alert(`写入成功：${res.data.saved} 条`)
      setPreview(null)
      setTaskId(null)
      setFile(null)
    } catch (e) {
      alert('确认失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  return (
    <div>
      <div className="raised" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select className="ig" value={selectedIndex} onChange={e => setSelectedIndex(e.target.value)}>
            <option value="">选择指数</option>
            {indexList.map(idx => (
              <option key={idx.code} value={idx.code}>{idx.name} ({idx.code})</option>
            ))}
          </select>
          <input type="date" className="ig" value={asOfDate} onChange={e => setAsOfDate(e.target.value)} />
          <input type="file" accept=".pdf" onChange={e => setFile(e.target.files[0])} />
          <button className="btn-ghost" onClick={handleUpload} disabled={uploading}>
            {uploading ? '解析中...' : '上传解析'}
          </button>
        </div>
        {error && <div style={{ color: 'red', marginTop: 8 }}>{error}</div>}
      </div>

      {preview && (
        <div className="raised" style={{ padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <strong>解析结果预览（{preview.length} 条）</strong>
            <button className="btn-ghost" onClick={handleConfirm}>确认写入</button>
          </div>
          <div style={{ maxHeight: 400, overflow: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr><th>股票代码</th><th>股票名称</th><th>权重</th></tr>
              </thead>
              <tbody>
                {preview.slice(0, 100).map((c, i) => (
                  <tr key={i}>
                    <td>{c.stock_code}</td>
                    <td>{c.stock_name}</td>
                    <td>{c.weight ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {preview.length > 100 && <div style={{ padding: 8, color: 'var(--text-muted)' }}>仅显示前 100 条，共 {preview.length} 条</div>}
          </div>
        </div>
      )}
    </div>
  )
}
