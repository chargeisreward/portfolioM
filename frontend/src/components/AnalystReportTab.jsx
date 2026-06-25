import React, { useState } from 'react'
import { rawApi as api } from '../api'

/**
 * 股票分析报告上传 tab。
 * 多文件选择 → 上传 → 显示每文件状态。
 */
export default function AnalystReportTab() {
  const [files, setFiles] = useState([])
  const [uploading, setUploading] = useState(false)
  const [results, setResults] = useState(null)

  /** 上传文件。 */
  const handleUpload = async () => {
    if (files.length === 0) { alert('请选择文件'); return }
    setUploading(true)
    setResults(null)
    try {
      const formData = new FormData()
      files.forEach(f => formData.append('files', f))
      const res = await api.post('/admin/upload/analyst-report', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResults(res.data.results)
    } catch (e) {
      alert('上传失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <div className="raised" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="file" multiple accept=".docx" onChange={e => setFiles(Array.from(e.target.files))} />
          <button className="btn-ghost" onClick={handleUpload} disabled={uploading}>
            {uploading ? '上传中...' : '上传'}
          </button>
        </div>
        <div style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: 12 }}>
          文件名需包含股票代码（6 位数字 + .SH/.SZ/.HK），如 "688041.SH公司研究框架.docx"
        </div>
      </div>

      {results && (
        <div className="raised" style={{ padding: 16 }}>
          <strong>上传结果</strong>
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr><th>文件名</th><th>股票代码</th><th>状态</th><th>错误</th></tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i}>
                  <td>{r.filename}</td>
                  <td>{r.stock_code || '-'}</td>
                  <td style={{ color: r.status === 'success' ? 'green' : 'red' }}>{r.status}</td>
                  <td>{r.error || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
