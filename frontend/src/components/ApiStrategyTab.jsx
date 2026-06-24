import React from 'react'
import StrategiesPanel from './StrategiesPanel'

/**
 * API策略 tab — 复用现有 StrategiesPanel。
 * StrategiesPanel 已实现：数据源清单 + 代码实时扫描 + 代码映射表 +
 * 覆盖率 + 调度任务实时状态 + 数据新鲜度 + 数据预览。
 */
export default function ApiStrategyTab() {
  return <StrategiesPanel />
}
