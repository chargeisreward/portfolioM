import React, { useState } from 'react'
import { rawApi as api } from '../api'

/**
 * 产业链报告上传 tab。
 * 输入产业链名称 → 上传总结 + 公司清单 → 显示结果。
 */
export default function IndustryChainTab() {
  const [chainName, setChainName] = useState('')
  const [summaryFile, setSummaryFile] = useState(null)
  const [companyFile, setCompanyFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)

  /** 上传。 */
  const handleUpload = async () => {
    if (!chainName) { alert('请输入产业链名称'); return }
    if (!summaryFile || !companyFile) { alert('请选择两个文件'); return }
    setUploading(true)
    setResult(null)
    try {
      const formData = new FormData()
      formData.append('chain_name', chainName)
      formData.append('summary_file', summaryFile)
      formData.append('company_list_file', companyFile)
      const res = await api.post('/admin/upload/industry-chain', formData, {
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
    <div>
      <div className="raised" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input className="ig" placeholder="产业链名称（如：AI产业链）" value={chainName} onChange={e => setChainName(e.target.value)} />
          <div>
            <label style={{ marginRight: 8 }}>总结报告 MD：</label>
            <input type="file" accept=".md" onChange={e => setSummaryFile(e.target.files[0])} />
          </div>
          <div>
            <label style={{ marginRight: 8 }}>公司清单 MD：</label>
            <input type="file" accept=".md" onChange={e => setCompanyFile(e.target.files[0])} />
          </div>
          <button className="btn-ghost" onClick={handleUpload} disabled={uploading} style={{ alignSelf: 'flex-start' }}>
            {uploading ? '上传中...' : '上传'}
          </button>
        </div>
      </div>

      {result && (
        <div className="raised" style={{ padding: 16 }}>
          <strong>上传结果</strong>
          <div style={{ marginTop: 8 }}>产业链保存：{result.chain_saved ? '成功' : '失败'}</div>
          <div>公司清单保存：{result.companies_saved} 条</div>
        </div>
      )}
    </div>
  )
}
