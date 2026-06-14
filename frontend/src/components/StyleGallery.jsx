import React from 'react'
import { useTheme } from '../ThemeContext'

/* ─── Style Preview Component ─── */
function StylePreview({ themeKey, theme, isActive, onSelect }) {
  const vars = theme.vars

  return (
    <div
      onClick={() => onSelect(themeKey)}
      style={{
        background: vars['--bg-primary'],
        borderRadius: vars['--card-radius'],
        padding: 16,
        cursor: 'pointer',
        border: isActive ? `2px solid ${vars['--accent-primary']}` : `2px solid transparent`,
        transition: 'all 0.3s ease',
        transform: isActive ? 'scale(1.02)' : 'scale(1)',
        boxShadow: isActive ? `0 0 30px ${vars['--accent-primary']}44` : 'none',
      }}
    >
      {/* Preview header */}
      <div style={{ fontSize: 14, fontWeight: 700, color: vars['--text-primary'], marginBottom: 4 }}>
        {theme.name}
        {isActive && <span style={{ marginLeft: 8, fontSize: 11, color: vars['--accent-primary'] }}>● 使用中</span>}
      </div>
      <div style={{ fontSize: 11, color: vars['--text-secondary'], marginBottom: 12 }}>{theme.desc}</div>

      {/* Preview card mockup */}
      <div style={{
        background: vars['--bg-glass'],
        backdropFilter: vars['--glass-blur'],
        WebkitBackdropFilter: vars['--glass-blur'],
        border: `1px solid ${vars['--glass-border']}`,
        borderRadius: '12px',
        padding: 12,
        marginBottom: 8,
        boxShadow: vars['--card-shadow'],
      }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
          <div style={{
            width: 8, height: 8, borderRadius: '50%',
            background: `linear-gradient(135deg, ${vars['--accent-primary']}, ${vars['--accent-secondary']})`,
          }} />
          <div style={{ flex: 1, height: 8, borderRadius: 4, background: vars['--text-secondary'] + '44' }} />
          <div style={{ width: 40, height: 8, borderRadius: 4, background: vars['--accent-primary'] + '66' }} />
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <div style={{ flex: 1, height: 30, borderRadius: 6, background: vars['--accent-primary'] + '22', border: `1px solid ${vars['--glass-border']}` }} />
          <div style={{ flex: 1, height: 30, borderRadius: 6, background: vars['--accent-secondary'] + '22', border: `1px solid ${vars['--glass-border']}` }} />
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {[40, 60, 30, 50].map((w, i) => (
            <div key={i} style={{
              flex: w, height: 20,
              background: `linear-gradient(to top, ${vars['--accent-primary']}55, ${vars['--accent-secondary']}33)`,
              borderRadius: '4px 4px 0 0',
              border: `1px solid ${vars['--glass-border']}`,
            }} />
          ))}
        </div>
      </div>

      {/* Mini KPI row */}
      <div style={{ display: 'flex', gap: 6 }}>
        {['PE', '增长', '权重'].map((l, i) => (
          <div key={i} style={{
            flex: 1, textAlign: 'center', padding: '6px 4px',
            background: vars['--bg-glass'],
            borderRadius: 8, border: `1px solid ${vars['--glass-border']}`,
            fontSize: 11,
          }}>
            <div style={{ color: vars['--text-secondary'], fontSize: 9 }}>{l}</div>
            <div style={{ color: vars['--text-primary'], fontWeight: 700 }}>{['36.5x', '38.9%', '100%'][i]}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ─── Main Gallery ─── */
export default function StyleGallery() {
  const { theme, setTheme, themes } = useTheme()

  return (
    <div>
      <div className="section-title">🎨 视觉效果 — 选择你喜欢的液态玻璃风格</div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
        gap: 16,
      }}>
        {Object.entries(themes).map(([key, t]) => (
          <StylePreview
            key={key}
            themeKey={key}
            theme={t}
            isActive={theme === key}
            onSelect={setTheme}
          />
        ))}
      </div>
      <div style={{
        marginTop: 12,
        padding: '12px 16px',
        background: 'var(--bg-glass)',
        backdropFilter: 'var(--glass-blur)',
        borderRadius: 'var(--card-radius)',
        border: '1px solid var(--glass-border)',
        fontSize: 12,
        color: 'var(--text-secondary)',
      }}>
        💡 点击任一卡片切换全局主题，所有页面即时生效。CSS 变量驱动，无需重新加载。
      </div>
    </div>
  )
}
