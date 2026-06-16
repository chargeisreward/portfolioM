import axios from 'axios'

// Dev: '' → '/api' (vite proxy handles it)
// Prod: VITE_API_URL is the absolute backend URL
const baseURL = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL.replace(/\/$/, '')}/api`
  : '/api'

const api = axios.create({ baseURL, timeout: 30000 })

// 自动注入 session token（从 localStorage）
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('portfoliom_session')
  if (token) {
    config.headers['x-session-token'] = token
  }
  return config
})

// 401 拦截：清掉 token 触发 App 跳到登录页
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      // 避免无限递归
      if (!err.config?.url?.includes('/api/auth/')) {
        localStorage.removeItem('portfoliom_session')
        // 触发刷新
        if (!window.location.hash.includes('auth')) {
          window.location.reload()
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

// Strategies (API 策略页面)
export const getStrategies = () => api.get('/strategies').then(r => r.data)

// Trading calendar
export const getCalendarMonth = (market, year, month) => api.get('/calendar/month', { params: { market, year, month } }).then(r => r.data)
export const getCalendarRange = (market, start, end) => api.get('/calendar', { params: { market, start, end } }).then(r => r.data)

// API code map
export const getCodeMaps = (api) => api.get('/code-map', { params: api ? { api } : {} }).then(r => r.data)

// Trend
export const getTrend = (days = 90, target = 'CNY') => api.get('/trend', { params: { days, target } }).then(r => r.data)

// Auth
export const getAuthStatus = () => api.get('/auth/status').then(r => r.data)
export const login = (password) => api.post('/auth/login', { password }).then(r => r.data)
export const logout = () => api.post('/auth/logout').then(r => r.data)
