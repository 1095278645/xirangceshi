// utils/api.js 后端请求封装
const app = getApp()

const TOKEN_KEY = 'shop_access_token'
const BASEURL_KEY = 'shop_base_url'

// ---------- 后端地址（真机演示必须可配置） ----------
// 真机上 127.0.0.1 指向手机自己，连不到电脑上的后端；所以地址必须能在
// 「设置」页改，而不是只能改 app.js 重新编译。默认值仍取 app.js 的 baseUrl。
function getBaseUrl() {
  try {
    const saved = wx.getStorageSync(BASEURL_KEY)
    if (saved) return saved
  } catch (e) {}
  return (app && app.globalData && app.globalData.baseUrl) || ''
}

function setBaseUrl(url) {
  const v = (url || '').trim().replace(/\/+$/, '')   // 去掉结尾斜杠，避免拼出 //
  try {
    if (v) wx.setStorageSync(BASEURL_KEY, v)
    else wx.removeStorageSync(BASEURL_KEY)
  } catch (e) {}
  if (app && app.globalData) app.globalData.baseUrl = v || app.globalData.baseUrl
}

// 访问令牌：后端设置了 SHOP_ACCESS_TOKEN 才校验；未设置时留空即可（行为不变）
function getToken() {
  try { return wx.getStorageSync(TOKEN_KEY) || '' } catch (e) { return '' }
}

function setToken(t) {
  try {
    if (t) wx.setStorageSync(TOKEN_KEY, t)
    else wx.removeStorageSync(TOKEN_KEY)
  } catch (e) {}
}

function authHeader() {
  const t = getToken()
  return t ? { 'X-Shop-Token': t } : {}
}

// 连接类错误在页面上只提示一次，避免 onShow + onLoad 重复弹一串 toast；
// 业务类错误（4xx/5xx）每次都提示，因为那是用户刚刚的操作失败了。
let connToastShown = false

function reportFail(msg) {
  if (connToastShown) return
  connToastShown = true
  wx.showToast({ title: msg, icon: 'none', duration: 2500 })
}

function resetFailFlag() {
  connToastShown = false
}

// 连接是否已失败过（供页面显示常驻提示条，而不是只弹一次就消失）
function connectionBroken() {
  return connToastShown
}

function request(path, method = 'GET', data = {}) {
  return new Promise((resolve, reject) => {
    const base = getBaseUrl()
    if (!base) {
      const e = new Error('还没设置后端地址：请到「设置」页填写电脑的局域网地址')
      e.silent = true
      return reject(e)
    }
    wx.request({
      url: base + path,
      method,
      data,
      timeout: 20000,
      header: { 'content-type': 'application/json', ...authHeader() },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else if (res.statusCode === 401) {
          const e = new Error('需要访问令牌：请到「设置」页填写访问令牌')
          e.toast = true
          reject(e)
        } else {
          const e = new Error('请求失败：' + res.statusCode)
          e.toast = true
          reject(e)
        }
      },
      fail(err) {
        const e = new Error('连不上后端（' + base + '），请确认电脑已启动服务且与手机同一网络')
        e.toast = true
        reject(e)
      }
    })
  })
}

// 统一的「后台加载失败」处理：静默接口（首页汇总、洞察等）失败时也给出提示，
// 页面不再需要用空的 .catch(() => {}) 把错误吞掉而让演示时无从排查。
function reportError(err, opts) {
  const silent = opts && opts.silent
  if (!silent) reportFail((err && err.message) || '加载失败')
}

module.exports = {
  // 后端地址
  getBaseUrl,
  setBaseUrl,
  // 访问令牌
  getToken,
  setToken,
  authHeader,
  // 错误提示
  reportError,
  resetFailFlag,
  connectionBroken,
  // 记账
  createOrder: (text) => request('/api/orders', 'POST', { text }),
  todaySummary: () => request('/api/orders/today'),
  monthlySummary: () => request('/api/orders/monthly'),
  vouchers: () => request('/api/vouchers'),
  orderInsights: (year, month, refresh) =>
    request('/api/orders/insights', 'POST', { year, month, refresh: !!refresh }),

  // 熟客
  customers: () => request('/api/customers'),
  customerDetail: (id) => request('/api/customers/' + id),
  addMemory: (customerId, content) => request('/api/memories', 'POST', { customer_id: customerId, content }),
  customerInsight: (cid) => request('/api/customers/' + cid + '/insight', 'POST', {}),

  // 文案
  generateCopy: (data) => request('/api/copy', 'POST', data),

  // 提醒
  generateReminders: () => request('/api/reminders/generate', 'POST'),
  reminders: () => request('/api/reminders'),
  reminderDone: (id) => request('/api/reminders/' + id + '/done', 'POST'),

  // 设置（用户自填 API Key）
  getSettings: () => request('/api/settings'),
  saveSettings: (data) => request('/api/settings', 'POST', data),
  getProviders: () => request('/api/providers'),

  // 账本（省账通能力）
  transactions: (year, month) => request(`/api/transactions?year=${year}&month=${month}`),
  accountTitles: () => request('/api/account-titles'),
  taxVat: (quarterlyRevenue) => request('/api/tax/vat', 'POST', { quarterly_revenue: quarterlyRevenue }),
  taxSurtax: (vat, isSmall) => request('/api/tax/surtax', 'POST', { vat, is_small: isSmall }),
  taxPit: (salary, social, special) => request('/api/tax/pit', 'POST', {
    salary, social_insurance: social, special_deduction: special }),
  taxCit: (annualIncome, isSmall) => request('/api/tax/cit', 'POST', { annual_income: annualIncome, is_small: isSmall }),
  taxCalendar: (year, month) => request(`/api/tax/calendar?year=${year}&month=${month}`),
  taxAdvice: (quarterlyRevenue, refresh) =>
    request('/api/tax/advice', 'POST',
      { quarterly_revenue: quarterlyRevenue, refresh: !!refresh }),
  reportUrl: (year, month) => getBaseUrl() + `/api/report/monthly?year=${year}&month=${month}`,

  // 收款账户（二维码收付款流水同步）
  paySources: () => request('/api/payment/sources'),
  savePaySource: (data) => request('/api/payment/sources', 'POST', data),
  deletePaySource: (id) => request('/api/payment/sources/' + id, 'DELETE'),
  syncPaySource: (id) => request('/api/payment/sources/' + id + '/sync', 'POST'),
  payLogs: () => request('/api/payment/logs'),
  demoClear: () => request('/api/payment/demo-clear', 'POST'),
  syncAllPay: () => request('/api/payment/sync-all', 'POST'),

  // 单店模型（保本线先行）
  storePresets: () => request('/api/store/presets'),
  storeModel: (data) => request('/api/store/model', 'POST', data),
  storeDiagnosis: (data) => request('/api/store/diagnosis', 'POST', data),
  storeFromLedger: () => request('/api/store/from-ledger'),

  // 单店档案（存档复用）
  storeProfiles: () => request('/api/profiles'),
  saveStoreProfile: (data) => request('/api/store/profile', 'POST', data),
  loadStoreProfile: (id) => request('/api/profile/' + id),
  delStoreProfile: (id) => request('/api/profile/' + id, 'DELETE'),

  // 掌柜今日复盘（心跳）
  heartbeat: () => request('/api/heartbeat')
}