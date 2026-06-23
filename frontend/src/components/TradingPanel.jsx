import React from 'react'
import { postImport } from '../api'

/**
 * 交易维护 — 占位（用户自行在外部记录交易 → 通过「导入」上传 Excel 持仓）
 * 多用户升级后由 OpsPanel 接管管理员运维功能。
 */
export default function TradingPanel() {
  async function goImport() {
    if (!confirm('从 dataDir 导入最新 Excel 持仓？')) return
    try {
      const r = await postImport()
      alert(`导入完成：${r.message || r.count || ''}`)
    } catch (e) {
      alert(`导入失败：${e?.response?.data?.detail || e.message}`)
    }
  }

  return (
    <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>
      <h2>交易维护</h2>
      <p>本功能将在下一版本上线。</p>
      <p>当前请使用「导入」功能上传 Excel 持仓文件（请在券商/银行 App 导出后上传）。</p>
      <button onClick={goImport} style={{ padding: '8px 16px', marginTop: 16, cursor: 'pointer' }}>
        导入持仓 Excel
      </button>
      <div style={{ marginTop: 32, fontSize: 11, color: 'var(--text-muted)', maxWidth: 480, margin: '32px auto 0', lineHeight: 1.6 }}>
        管理员可在「运维」面板执行：导入 / 全量抓取 / 下钻 / 重算 CSI300 / 补齐价格。
      </div>
    </div>
  )
}
