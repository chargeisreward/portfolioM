import React from 'react'

/**
 * 占比可视化柱状条（连续渲染版本）。
 *
 * 规则：
 * - 5 格，每格代表 2%（满格 = 10%）
 * - 连续填充：pct=2.6 → 第1格满 + 第2格填 30%
 * - pct > 10（严格大于）→ 5 格全满 + 同色 "+" 符号
 * - pct === 10 → 5 格全满，不显示 "+"
 * - pct <= 0 → 5 格全空
 *
 * 用法：<ShareBar pct={2.6} />
 */
const ShareBar = ({ pct }) => {
  const TOTAL_CELLS = 5
  const PCT_PER_CELL = 2
  const safePct = typeof pct === 'number' && !isNaN(pct) ? pct : 0
  const isOverflow = safePct > 10  // 严格大于 10% 才显示 +
  const filledRatio = Math.max(0, Math.min(TOTAL_CELLS, safePct / PCT_PER_CELL))

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
      <span style={{ display: 'inline-flex' }}>
        {[0, 1, 2, 3, 4].map(i => {
          // 该格的填充比例：0~1
          const cellFill = Math.max(0, Math.min(1, filledRatio - i))
          return (
            <span
              key={i}
              style={{
                display: 'inline-block',
                width: '0.9em',
                height: '0.9em',
                border: '1px solid rgba(46,160,67,0.30)',
                background: 'rgba(46,160,67,0.10)',
                position: 'relative',
                boxSizing: 'border-box',
                overflow: 'hidden',
              }}
            >
              {cellFill > 0 && (
                <span
                  style={{
                    position: 'absolute',
                    left: 0,
                    top: 0,
                    height: '100%',
                    width: `${cellFill * 100}%`,
                    background: '#2ea043',
                  }}
                />
              )}
            </span>
          )
        })}
      </span>
      {isOverflow && (
        <span
          style={{
            color: '#2ea043',
            marginLeft: 3,
            fontSize: 14,
            fontWeight: 700,
            lineHeight: 1,
          }}
        >
          +
        </span>
      )}
    </span>
  )
}

export default ShareBar
