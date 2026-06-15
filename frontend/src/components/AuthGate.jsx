import React, { useState, useEffect } from 'react'
import * as api from '../api'

/**
 * 访问密码门 — 全屏空白页 + 一个输入框
 * 后端限流规则（同一 IP）：
 *   10 次错 → 禁 1h
 *   20 次错 → 禁 1d
 *   30 次错 → 禁 30d
 *   40 次错 → 禁 365d
 */
export default function AuthGate({ onLoggedIn }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [banned, setBanned] = useState(null)   // {until, remaining}
  const [attempts, setAttempts] = useState(0)
  const [submitting, setSubmitting] = useState(false)

  // 启动时拉一次 status（检查是否已被锁）
  useEffect(() => {
    api.getAuthStatus().then(s => {
      if (s.banned) {
        setBanned({ until: s.banned_until, remaining: s.remaining_seconds })
      }
    }).catch(() => {})
  }, [])

  const formatRemaining = (sec) => {
    if (sec >= 86400) return `${Math.ceil(sec / 86400)} 天`
    if (sec >= 3600) return `${Math.ceil(sec / 3600)} 小时`
    if (sec >= 60) return `${Math.ceil(sec / 60)} 分钟`
    return `${sec} 秒`
  }

  const submit = async (e) => {
    e?.preventDefault()
    if (banned || submitting) return
    if (password.length < 6) {
      setError('密码至少 6 位')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const res = await api.login(password)
      if (res.status === 'ok' && res.token) {
        onLoggedIn(res.token)
      } else if (res.status === 'banned') {
        setBanned({ until: res.banned_until, remaining: res.remaining_seconds })
        setError(res.message || '已被封禁')
      } else {
        setError(res.message || '密码错误')
        setAttempts(res.attempts_1y || 0)
      }
    } catch (e) {
      const status = e?.response?.status
      if (status === 401 || status === 403) {
        setError('密码错误')
      } else if (e?.response?.data?.detail?.includes('封禁')) {
        setError(e.response.data.detail)
      } else {
        setError('网络错误：' + (e?.message || e))
      }
    }
    setSubmitting(false)
  }

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'var(--bg)', color: 'var(--text)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: '"GeistMono", monospace',
    }}>
      <form onSubmit={submit} style={{
        width: 360, padding: 32, border: '1px solid var(--border)',
        background: 'var(--bg-raised)',
      }}>
        <div style={{
          fontSize: 11, color: 'var(--text-muted)', letterSpacing: 1.5,
          textTransform: 'uppercase', marginBottom: 6,
        }}>PortfolioM</div>
        <h1 style={{
          fontSize: 20, fontWeight: 400, color: 'var(--text)', margin: '0 0 24px 0',
          letterSpacing: 0.5,
        }}>访问需要密码</h1>

        {banned ? (
          <div style={{
            padding: '12px', background: 'rgba(220,38,38,0.1)',
            border: '1px solid var(--down)', color: 'var(--down)',
            fontSize: 13, marginBottom: 16,
          }}>
            <div style={{ marginBottom: 4 }}>⛔ 此 IP 已被封禁</div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
              剩余 <span style={{ fontWeight: 600 }}>{formatRemaining(banned.remaining)}</span>
            </div>
          </div>
        ) : (
          <>
            <input
              type="password"
              className="ig"
              autoFocus
              value={password}
              onChange={e => { setPassword(e.target.value); setError('') }}
              placeholder="6-12 位密码"
              disabled={submitting}
              style={{ width: '100%', marginBottom: 12, fontSize: 14, padding: '10px 12px' }}
            />
            {error && (
              <div style={{
                color: 'var(--down)', fontSize: 12, marginBottom: 12,
                padding: '6px 10px', background: 'rgba(220,38,38,0.08)',
                border: '1px solid rgba(220,38,38,0.2)',
              }}>{error}</div>
            )}
            {attempts > 0 && !error && (
              <div style={{ color: 'var(--text-muted)', fontSize: 11, marginBottom: 12 }}>
                输错 {attempts} 次（10 次开始封禁）
              </div>
            )}
            <button
              type="submit"
              disabled={submitting || !password}
              className="btn-ghost"
              style={{ width: '100%', padding: '10px', fontSize: 13 }}
            >
              {submitting ? '验证中…' : '进入'}
            </button>
          </>
        )}

        <div style={{
          marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--border)',
          fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.6,
        }}>
          <div>封禁规则：</div>
          <div>10 次 → 1h · 20 次 → 1d · 30 次 → 30d · 40 次 → 365d</div>
        </div>
      </form>
    </div>
  )
}
