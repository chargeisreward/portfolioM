import React, { useState, useEffect } from 'react'
import * as api from '../api'

/**
 * 多用户登录门 — username + password (bcrypt)
 * 后端限流规则（同一 IP）：
 *   10 次错 → 禁 1h
 *   20 次错 → 禁 1d
 *   30 次错 → 禁 30d
 *   40 次错 → 禁 365d
 */
export default function AuthGate({ onLoggedIn }) {
  const [username, setUsername] = useState('')
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
    if (username.length < 3) {
      setError('用户名至少 3 位')
      return
    }
    if (password.length < 6) {
      setError('密码至少 6 位')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const res = await api.login(username, password)
      if (res.status === 'ok' && res.token) {
        // token 由后端 Set-Cookie 管理（HttpOnly），前端不存储
        // res.token 仅用于判断登录成功，不持久化
        onLoggedIn(res.token, res.user)
      } else if (res.status === 'banned') {
        setBanned({ until: res.banned_until, remaining: res.remaining_seconds })
        setError(res.message || '已被封禁')
      } else {
        setError(res.message || '登录失败')
        setAttempts(res.attempts_1y || 0)
      }
    } catch (e) {
      const status = e?.response?.status
      if (status === 401 || status === 403) {
        setError('用户名或密码错误')
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
        }}>登录</h1>

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
              type="text"
              className="ig"
              autoFocus
              value={username}
              onChange={e => { setUsername(e.target.value); setError('') }}
              placeholder="用户名"
              disabled={submitting}
              autoComplete="username"
              style={{ width: '100%', marginBottom: 12, fontSize: 14, padding: '10px 12px' }}
            />
            <input
              type="password"
              className="ig"
              value={password}
              onChange={e => { setPassword(e.target.value); setError('') }}
              placeholder="6 位以上密码"
              disabled={submitting}
              autoComplete="current-password"
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
              disabled={submitting}
              className="btn-ghost"
              style={{ width: '100%', padding: '10px', fontSize: 13 }}
            >
              {submitting ? '验证中…' : '登录'}
            </button>
          </>
        )}

        <div style={{
          marginTop: 24, paddingTop: 16, borderTop: '1px solid var(--border)',
          fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.6,
        }}>
          <div>测试账户：</div>
          <div>admin / admin123 · advisor_x / advisor123 · user_a / user123</div>
        </div>
      </form>
    </div>
  )
}
