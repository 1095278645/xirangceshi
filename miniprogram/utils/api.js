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

// 当前店铺：多店时后端按 X-Shop-Id 决定数据落哪家店。
// 单店/演示环境不设置即可（后端会落到默认店），行为与改造前一致。
const SHOP_KEY = 'shop_current_id'

function getShopId() {
  try {
    const v = wx.getStorageSync(SHOP_KEY)
    return v === '' || v === undefined || v === null ? null : Number(v)
  } catch (e) { return null }
}

function setShopId(id) {
  try {
    if (id === null || id === undefined || id === '') wx.removeStorageSync(SHOP_KEY)
    else wx.setStorageSync(SHOP_KEY, Number(id))
  } catch (e) {}
}

function authHeader() {
  const h = {}
  const t = getToken()
  if (t) h['X-Shop-Token'] = t
  const s = getShopId()
  if (s !== null && !isNaN(s)) h['X-Shop-Id'] = String(s)
  return h
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
        } else if (res.statusCode === 403) {
          // 403 是业务性拒绝（如"你不是这家店的成员"），必须把后端原话透出来，
          // 否则演示时只看到"请求失败：403"，无从判断该切店还是该找店主开权限。
          const d = res.data && res.data.detail
          const e = new Error(d || '没有权限执行该操作')
          e.toast = true
          e.forbidden = true
          reject(e)
        } else {
          const d = res.data && res.data.detail
          const e = new Error(d || ('请求失败：' + res.statusCode))
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
  // 记账。extra 用于"补金额"场景：把 AI 已解析好的字段原样带回，
  // 后端据此跳过重解析（科目/熟客不会漂移）
  createOrder: (text, extra) => request('/api/orders', 'POST', { text, ...(extra || {}) }),
  todaySummary: () => request('/api/orders/today'),
  monthlySummary: () => request('/api/orders/monthly'),
  vouchers: () => request('/api/vouchers'),
  orderInsights: (year, month, refresh) =>
    request('/api/insights', 'POST', { scene: 'monthly', payload: { year, month }, refresh: !!refresh }),

  // 熟客
  customers: () => request('/api/customers'),
  customerDetail: (id) => request('/api/customers/' + id),
  addMemory: (customerId, content) => request('/api/memories', 'POST', { customer_id: customerId, content }),
  customerInsight: (cid) => request('/api/insights', 'POST', { scene: 'customer', payload: { customer_id: cid } }),

  // 文案
  generateCopy: (data) => request('/api/insights', 'POST', { scene: 'copy', payload: data }),

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
    request('/api/insights', 'POST',
      { scene: 'tax', payload: { quarterly_revenue: quarterlyRevenue }, refresh: !!refresh }),
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
  storeDiagnosis: (data) => request('/api/insights', 'POST', { scene: 'store', payload: data }),
  storeFromLedger: () => request('/api/store/from-ledger'),

  // 单店档案（存档复用）
  storeProfiles: () => request('/api/profiles'),
  saveStoreProfile: (data) => request('/api/store/profile', 'POST', data),
  loadStoreProfile: (id) => request('/api/profile/' + id),
  delStoreProfile: (id) => request('/api/profile/' + id, 'DELETE'),

  // 掌柜今日复盘（心跳）。POST 会让掌柜立刻复盘一次（多 agent，约 5~10 秒）
  heartbeat: () => request('/api/heartbeat'),
  reviewNow: () => request('/api/heartbeat', 'POST', {}),
  reviewFeedback: (useful, reason) =>
    request('/api/heartbeat/feedback', 'POST', { useful, reason: reason || '' }),
  shopSnapshot: () => request('/api/heartbeat/snapshot'),

  // ---- 资金健康：现金流预测 / 预算 / 应收应付 ----
  cashflow: (data) => request('/api/cashflow', 'POST', data),
  budgets: (month) => request('/api/budgets?month=' + month),
  saveBudget: (data) => request('/api/budgets', 'POST', data),
  deleteBudget: (id) => request('/api/budgets/' + id, 'DELETE'),
  budgetActual: (month) => request('/api/budgets/actual?month=' + month),
  debts: () => request('/api/debts'),
  addDebt: (data) => request('/api/debts', 'POST', data),
  settleDebt: (id) => request('/api/debts/' + id + '/settle', 'POST', {}),
  deleteDebt: (id) => request('/api/debts/' + id, 'DELETE'),
  debtsAging: () => request('/api/debts/aging'),

  // ---- 库存进销存 ----
  stock: () => request('/api/stock'),
  products: () => request('/api/products'),
  saveProduct: (data) => request('/api/products', 'POST', data),
  deleteProduct: (id) => request('/api/products/' + id, 'DELETE'),
  moveStock: (id, movement, qty, note) =>
    request('/api/products/' + id + '/move', 'POST', { movement, qty, note: note || '' }),

  // ---- 发票台账 ----
  invoices: () => request('/api/invoices'),
  invoiceSummary: () => request('/api/invoices/summary'),
  saveInvoice: (data) => request('/api/invoices', 'POST', data),
  voidInvoice: (id) => request('/api/invoices/' + id + '/void', 'POST', {}),

  // ---- 多店 / 多用户 ----
  shopContext: () => request('/api/shops/context'),
  listShops: () => request('/api/shops'),
  createShop: (data) => request('/api/shops', 'POST', data),
  updateShop: (id, data) => request('/api/shops/' + id, 'PUT', data),
  deleteShop: (id, purge) => request('/api/shops/' + id + (purge ? '?purge=true' : ''), 'DELETE'),
  switchShop: (id) => request('/api/shops/switch', 'POST', { shop_id: id }),
  listUsers: () => request('/api/shops/users'),
  createUser: (data) => request('/api/shops/users', 'POST', data),
  updateUser: (id, data) => request('/api/shops/users/' + id, 'PUT', data),
  deleteUser: (id) => request('/api/shops/users/' + id, 'DELETE'),
  shopMembers: (shopId) => request('/api/shops/' + shopId + '/members'),
  grantMember: (shopId, userId, role) =>
    request('/api/shops/' + shopId + '/members', 'POST', { user_id: userId, role }),
  revokeMember: (shopId, userId) =>
    request('/api/shops/' + shopId + '/members/' + userId, 'DELETE'),

  // ---- 备份 / 导出 / 恢复 ----
  backups: () => request('/api/backup/list'),
  createBackup: (kind) => request('/api/backup/create', 'POST', { kind: kind || 'manual' }),
  deleteBackup: (name) => request('/api/backup/' + encodeURIComponent(name), 'DELETE'),
  backupDownloadUrl: (name) =>
    getBaseUrl() + '/api/backup/download/' + encodeURIComponent(name) + tokenQuery(),
  exportUrl: () => getBaseUrl() + '/api/backup/export' + tokenQuery(),

  // ---- 交易更正（编辑 / 作废 / 退货冲销） ----
  editTransaction: (id, data) => request('/api/transactions/' + id, 'POST', data),
  voidTransaction: (id, reason) =>
    request('/api/transactions/' + id + '/void', 'POST', { reason: reason || '' }),
  refundTransaction: (id, data) =>
    request('/api/transactions/' + id + '/refund', 'POST', data || {}),
  transactionAudits: (limit) => request('/api/audits?limit=' + (limit || 50)),

  // ---- 收款即入账（收款码 / 到账播报） ----
  collections: (status) =>
    request('/api/collect/list' + (status ? '?status=' + status : '')),
  createCollection: (data) => request('/api/collect/create', 'POST', data),
  confirmCollection: (id) => request('/api/collect/' + id + '/confirm', 'POST', {}),
  cancelCollection: (id, reason) =>
    request('/api/collect/' + id + '/cancel', 'POST', { reason: reason || '' }),
  collectionQrUrl: (token) =>
    getBaseUrl() + '/api/collect/' + token + '/qr.png',
  payPageUrl: (token) => getBaseUrl() + '/pay/' + token,

  // ---- 主动触达（订阅消息 / 推送） ----
  // 注意：后端字段叫 channel（不是 provider），试发还要求带 channel —— 漏了会 422。
  notifyProviders: () => request('/api/notify/providers'),
  notifyEvents: () => request('/api/notify/events'),
  notifySubscriptions: () => request('/api/notify/subscriptions'),
  saveNotifySubscription: (data) => request('/api/notify/subscriptions', 'POST', data),
  deleteNotifySubscription: (id) =>
    request('/api/notify/subscriptions/' + id, 'DELETE'),
  testNotifySubscription: (id) =>
    request('/api/notify/subscriptions/' + id + '/test', 'POST', {}),
  notifyTest: (channel, data) =>
    request('/api/notify/test', 'POST', { channel, ...(data || {}) }),
  notifyLogs: () => request('/api/notify/logs'),
  notifyMockInbox: () => request('/api/notify/mock-inbox'),
  notifyWecomBot: (key) => request('/api/notify/wecom-bot', 'POST', { key }),

  // ---- 会计闭环（三表 + 期末结转） ----
  trialBalance: (period, excludeClosing) =>
    request('/api/accounting/trial-balance' +
            (period ? '?period=' + encodeURIComponent(period) : '') +
            (excludeClosing ? (period ? '&' : '?') + 'exclude_closing=true' : '')),
  incomeStatement: (period) =>
    request('/api/accounting/income-statement' +
            (period ? '?period=' + encodeURIComponent(period) : '')),
  // 注意：资产负债表是按日期切片的（as_of），不是按会计期间
  balanceSheet: (asOf) =>
    request('/api/accounting/balance-sheet' +
            (asOf ? '?as_of=' + encodeURIComponent(asOf) : '')),
  closePeriod: (period, note) =>
    request('/api/accounting/close', 'POST', { period, note: note || '' }),
  reopenPeriod: (period, note) =>
    request('/api/accounting/reopen', 'POST', { period, note: note || '' }),
  closings: () => request('/api/accounting/closings'),
  openingBalances: () => request('/api/accounting/opening-balances'),
  saveOpeningBalance: (data) => request('/api/accounting/opening-balances', 'POST', data),

  // 通用请求（管理台里恢复备份等一次性调用，避免为每个端点都写包装）
  raw: (path, method, data) => request(path, method || 'GET', data || {})
}

// 报表/下载类链接带令牌：浏览器直接打开 URL 时无法自定义请求头
function tokenQuery() {
  const t = getToken()
  return t ? '?token=' + encodeURIComponent(t) : ''
}
