import axios from 'axios'

// Dev: base='/' → '/api' (vite proxy handles it)
// Prod: base='/portfoliom/' → '/portfoliom/api' (system nginx proxies to backend)
// Override: VITE_API_URL for absolute backend URL
const baseURL = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL.replace(/\/$/, '')}/api`
  : `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api`

// withCredentials=true：跨域请求携带 cookie（HttpOnly session_token）
// token 不再存 localStorage，由后端 Set-Cookie 管理，JS 不可读，防 XSS 窃取
const api = axios.create({ baseURL, timeout: 30000, withCredentials: true })

// view_as 内存变量（不持久化，刷新后重置）
let _viewAsUserId = null

/**
 * 设置 view_as 用户 ID（内存中，不持久化到 localStorage）
 * @param {number|null} userId - 视图代理目标用户 ID，null 表示查看自己
 */
export function setViewAs(userId) {
  _viewAsUserId = userId
}

// 401 未授权回调（App 注册后，401 时触发跳转登录页）
let _onUnauthorized = null

/**
 * 注册 401 未授权回调
 * @param {() => void} callback - 401 时调用，App 用于重置状态显示 AuthGate
 */
export function onUnauthorized(callback) {
  _onUnauthorized = callback
}

// 请求拦截：注入 view_as（从内存变量，不再读 localStorage）
api.interceptors.request.use((config) => {
  if (_viewAsUserId) {
    config.params = { ...(config.params || {}), view_as: _viewAsUserId }
  }
  return config
})

// 401 响应拦截：通知 App 重置状态（不再操作 localStorage，不再 reload）
// 防抖：短时间内多个 401 只触发一次回调
let _unauthScheduled = false
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      // 排除 /api/auth/ 路径（登录/登出接口的 401 不触发跳转）
      if (!err.config?.url?.includes('/api/auth/')) {
        if (_onUnauthorized && !_unauthScheduled) {
          _unauthScheduled = true
          setTimeout(() => {
            _unauthScheduled = false
            _onUnauthorized && _onUnauthorized()
          }, 100)
        }
      }
    }
    return Promise.reject(err)
  }
)

export const getHoldingsSummary = () => api.get('/holdings/summary').then(r => r.data)
export const getPenetrationTable = () => api.get('/penetration/table').then(r => r.data)
export const getPenetrationSummary = () => api.get('/penetration/summary').then(r => r.data)
export const getIndustryChain = () => api.get('/analysis/industry-chain').then(r => r.data)
export const getGrowthAnalysis = () => api.get('/analysis/growth').then(r => r.data)
export const getValuation = () => api.get('/analysis/valuation').then(r => r.data)
export const getPrices = (codes, days = 90) => api.get('/prices', { params: { codes, days } }).then(r => r.data)
export const getBondPrices = (days = 365) => api.get('/prices/bonds', { params: { days } }).then(r => r.data)
export const rawApi = api
export const getHoldingsConverted = (target = 'CNY') => api.get('/holdings/converted', { params: { target } }).then(r => r.data)
export const postFillPrices = () => api.post('/holdings/fill-prices').then(r => r.data)
export const postImport = () => api.post('/holdings/import', {}).then(r => r.data)
export const postCrawlAll = () => api.post('/crawl/all').then(r => r.data)
export const postPenetration = () => api.post('/penetration/calculate').then(r => r.data)
export const postRecalcCsi300 = () => api.post('/csi300/recalc').then(r => r.data)
export const postSyncSecurities = () => api.post('/securities/sync-from-holdings').then(r => r.data)
export const getSecurities = () => api.get('/securities').then(r => r.data)
export const upsertSecurity = (code, data) => api.put(`/securities/${code}`, data).then(r => r.data)
export const getSecurityTypes = () => api.get('/security-types').then(r => r.data)
export const seedSecurityTypes = () => api.post('/security-types/seed').then(r => r.data)
export const getDataTables = () => api.get('/data-browser/tables').then(r => r.data)
export const browseTable = (table, page = 1, pageSize = 50) => api.get(`/data-browser/${table}`, { params: { page, page_size: pageSize } }).then(r => r.data)

// Watchlist
export const getWatchlist = () => api.get('/watchlist').then(r => r.data)
export const addWatchlist = (code) => api.post('/watchlist', { code }).then(r => r.data)
export const removeWatchlist = (code) => api.delete(`/watchlist/${encodeURIComponent(code)}`).then(r => r.data)
export const setWatchlistWeight = (code, weight) => api.put(`/watchlist/${encodeURIComponent(code)}/weight`, { weight }).then(r => r.data)
export const searchSecurities = (q) => api.get('/watchlist/search', { params: { q } }).then(r => r.data)

// Data browser edit
export const getDataBrowserOptions = () => api.get('/data-browser/options').then(r => r.data)
export const updateTableRow = (table, pkCol, pkVal, body) => api.put(`/data-browser/${table}/${pkCol}/${pkVal}`, body).then(r => r.data)

// 数据质量仪表盘
export const getDataOverview = () => api.get('/data-browser/overview').then(r => r.data)
export const getTableStats = (table) => api.get(`/data-browser/${table}/stats`).then(r => r.data)
export const getDataSchema = () => api.get('/data-browser/schema').then(r => r.data)

// Strategies (API 策略页面)
export const getStrategies = () => api.get('/strategies').then(r => r.data)

// Scheduler 实时状态 + 数据新鲜度 + 数据预览
export const getSchedulerStatus = () => api.get('/scheduler/status').then(r => r.data)
export const triggerSchedulerJob = (jobId, force = false, background = true) =>
  api.post(`/scheduler/trigger/${jobId}`, null, { params: { force, background } }).then(r => r.data)
export const getDataFreshness = () => api.get('/data-freshness').then(r => r.data)
export const getDataPreview = (table, opts = {}) => {
  const params = { table, limit: opts.limit ?? 20 }
  if (opts.stock_code) params.stock_code = opts.stock_code
  return api.get('/data-preview', { params }).then(r => r.data)
}

// Trading calendar
export const getCalendarMonth = (market, year, month) => api.get('/calendar/month', { params: { market, year, month } }).then(r => r.data)
export const getCalendarRange = (market, start, end) => api.get('/calendar', { params: { market, start, end } }).then(r => r.data)

// API code map
export const getCodeMaps = (apiStrategy) => api.get('/code-map', { params: apiStrategy ? { api: apiStrategy } : {} }).then(r => r.data)
export const getCodeMapCoverage = (pool = 'all', apiStrategy = null) =>
  api.get('/code-map/coverage', {
    params: { pool, ...(apiStrategy ? { api: apiStrategy } : {}) },
  }).then(r => r.data)

// Trend (force=true 触发三级回退自愈，补齐 360 天覆盖)
export const getTrend = (days = 90, target = 'CNY', force = false) => api.get('/trend', { params: { days, target, force } }).then(r => r.data)

// Auth
export const getAuthStatus = () => api.get('/auth/status').then(r => r.data)
export const login = (username, password) => api.post('/auth/login', { username, password }).then(r => r.data)
// 兼容旧调用（仅密码 → 旧单密码模式）
export const loginPasswordOnly = (password) => api.post('/auth/login', { password }).then(r => r.data)
export const getAuthMe = () => api.get('/auth/me').then(r => r.data.user)
export const getUsers = () => api.get('/auth/users').then(r => r.data)
export const logout = () => api.post('/auth/logout').then(r => r.data)

// 关联管理
export const listRelations = () => api.get('/auth/relations').then(r => r.data)
export const createRelation = (body) => api.post('/auth/relations', body).then(r => r.data)
export const confirmRelation = (id) => api.post(`/auth/relations/${id}/confirm`).then(r => r.data)
export const cancelRelation = (id) => api.post(`/auth/relations/${id}/cancel`).then(r => r.data)

// 数据补足（admin）
export const getGapReport = (params = {}) => api.get('/data-gap/report', { params }).then(r => r.data)
export const fixGap = (id) => api.post(`/data-gap/fix/${id}`).then(r => r.data)
export const setIndexClassification = (body) => api.post('/data-gap/index-classification', body).then(r => r.data)

// ============================================================================
// Fund Penetration & Industry Aggregation (spec §4)
// ============================================================================

export const getDataVersion = () => api.get('/data-version').then(r => r.data)
export const getAnalystCoreCompanies = (asOfDate) => api.get('/analyst/core-companies', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const getAnalystStockDetail = (code, asOfDate) => api.get(`/analyst/stock/${encodeURIComponent(code)}`, { params: { as_of_date: asOfDate } }).then(r => r.data)
export const getAnalystIndustryChains = (asOfDate) => api.get('/analyst/industry-chains', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const postAnalystIngest = () => api.post('/admin/analyst/ingest').then(r => r.data)
export const getFullHolding = (asOfDate) => api.get('/penetration/full-holding', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const getDimension = (dim, asOfDate, market = 'A+H') => api.get('/penetration/dimension', { params: { dim, as_of_date: asOfDate, market } }).then(r => r.data)
export const getDimensionDetail = (dim, key, asOfDate, market = 'A+H') => api.get('/penetration/dimension-detail', { params: { dim, key, as_of_date: asOfDate, market } }).then(r => r.data)
export const getTimeseries = (scope = 'both', metric = 'pe_weighted', window = 90) =>
  api.get('/penetration/timeseries', { params: { scope, metric, window } }).then(r => r.data)
export const getKpi = (asOfDate) => api.get('/penetration/kpi', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const importSourceData = (sourceFolder) => api.post('/admin/import-source-data', null, { params: { source_folder: sourceFolder } }).then(r => r.data)
export const recalcAggregation = (asOfDate) => api.post('/admin/recalc-aggregation', null, { params: { as_of_date: asOfDate } }).then(r => r.data)

export const getPortfolioVsCsi300 = (asOfDate) => api.get('/penetration/portfolio-vs-csi300', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const getFullHoldingSummary = (asOfDate) => api.get('/penetration/full-holding-summary', { params: { as_of_date: asOfDate } }).then(r => r.data)

export const getDrillableIndices = (asOfDate) => api.get('/penetration/drillable-indices', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const getIndexDrill = (indexCode, asOfDate) => api.get('/penetration/index-drill', { params: { index_code: indexCode, as_of_date: asOfDate } }).then(r => r.data)
export const getAllDrilledStocks = (asOfDate) => api.get('/penetration/all-drilled-stocks', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const getFullHoldingTable = (asOfDate) => api.get('/penetration/full-holding-table', { params: { as_of_date: asOfDate } }).then(r => r.data)
export const getTop10Holdings = (asOfDate, limit = 10) => api.get('/penetration/top10-holdings', { params: { as_of_date: asOfDate, limit } }).then(r => r.data)
export const getDimensionDrilled = (dim, asOfDate, market = 'A+H') => api.get('/penetration/dimension-drilled', { params: { dim, as_of_date: asOfDate, market } }).then(r => r.data)
export const getLatestExchangeRates = () => api.get('/exchange-rates/latest').then(r => r.data)

// ---------- 交易记录驱动的持仓重建 (2026-06-26) ----------
export const parseTrades = (text) =>
  api.post('/trades/parse', { text }).then(r => r.data)

export const confirmTrades = (trades) =>
  api.post('/trades/confirm', { trades }).then(r => r.data)

export const getTrades = (params) =>
  api.get('/trades', { params }).then(r => r.data)

export const updateTrade = (tradeId, trade) =>
  api.put(`/trades/${tradeId}`, trade, { timeout: 120000 }).then(r => r.data)

export const deleteTrade = (tradeId) =>
  api.delete(`/trades/${tradeId}`, { timeout: 120000 }).then(r => r.data)

export const getTradingSession = () =>
  api.get('/trading-session').then(r => r.data)

export const getSnapshot = (params) =>
  api.get('/holdings/snapshot', { params }).then(r => r.data)

export const getSnapshotRange = () =>
  api.get('/holdings/snapshot-range').then(r => r.data)

export const getDailyTrades = (params) =>
  api.get('/holdings/daily-trades', { params }).then(r => r.data)

// ---------- 估值表日截面（2026-06-27）----------
// 数据源：valuation_daily_snapshot 表（按 user_id 隔离，含锁定状态）
export const getValuationSnapshot = (params) =>
  api.get('/valuation/snapshot', { params }).then(r => r.data)

export const getValuationRange = () =>
  api.get('/valuation/snapshot-range').then(r => r.data)

export const postValuationRebuild = (data = {}) =>
  api.post('/valuation/rebuild', data).then(r => r.data)

// ---------- 管理员价格刷新（2026-06-26）----------
// 全用户持仓并集 + 增量（TTL），公共数据层模式
export const adminFillPricesAll = () =>
  api.post('/admin/fill-prices-all', null, { timeout: 120000 }).then(r => r.data)

// 分析页全持仓收盘价增量刷新（下钻股票 NULL 填充 + 未下钻基金净值补缺）
export const adminRefreshAnalysisPrices = (asOfDate, days = 5, maxCodes = 200) =>
  api.post('/admin/refresh-analysis-prices', null, {
    params: { as_of_date: asOfDate, days, max_codes: maxCodes },
    timeout: 120000,
  }).then(r => r.data)
