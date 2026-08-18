// utils/api.js 后端请求封装
const app = getApp()

function request(path, method = 'GET', data = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: app.globalData.baseUrl + path,
      method,
      data,
      timeout: 20000,
      header: { 'content-type': 'application/json' },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else {
          reject(new Error('请求失败：' + res.statusCode))
        }
      },
      fail(err) {
        reject(new Error('无法连接小店服务，请确认后端已启动'))
      }
    })
  })
}

module.exports = {
  // 记账
  createOrder: (text) => request('/api/orders', 'POST', { text }),
  todaySummary: () => request('/api/orders/today'),
  monthlySummary: () => request('/api/orders/monthly'),
  vouchers: () => request('/api/vouchers'),

  // 熟客
  customers: () => request('/api/customers'),
  customerDetail: (id) => request('/api/customers/' + id),
  addMemory: (customerId, content) => request('/api/memories', 'POST', { customer_id: customerId, content }),

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
  reportUrl: (year, month) => app.globalData.baseUrl + `/api/report/monthly?year=${year}&month=${month}`,

  // 收款账户（二维码收付款流水同步）
  paySources: () => request('/api/payment/sources'),
  savePaySource: (data) => request('/api/payment/sources', 'POST', data),
  deletePaySource: (id) => request('/api/payment/sources/' + id, 'DELETE'),
  syncPaySource: (id) => request('/api/payment/sources/' + id + '/sync', 'POST'),
  payLogs: () => request('/api/payment/logs'),
  demoClear: () => request('/api/payment/demo-clear', 'POST'),
  syncAllPay: () => request('/api/payment/sync-all', 'POST')
}