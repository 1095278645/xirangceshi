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

  // 熟客
  customers: () => request('/api/customers'),
  customerDetail: (id) => request('/api/customers/' + id),
  addMemory: (customerId, content) => request('/api/memories', 'POST', { customer_id: customerId, content }),

  // 文案
  generateCopy: (data) => request('/api/copy', 'POST', data),

  // 提醒
  generateReminders: () => request('/api/reminders/generate', 'POST'),
  reminders: () => request('/api/reminders'),
  reminderDone: (id) => request('/api/reminders/' + id + '/done', 'POST')
}