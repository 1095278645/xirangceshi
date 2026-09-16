// pages/collect/collect.js 收款即入账：现场生成收款码 + 到账播报
//
// 演示动线：输入金额 → 生成收款码 → 顾客扫（或店主自己打开链接模拟"我已付款"）
// → 列表变成"待确认" → 一键入账（自动生成凭证 + 广播到账消息）。
const api = require('../../utils/api.js')

Page({
  data: {
    amount: '',
    item: '',
    list: [],
    pending: 0,
    qr: null,        // 当前展示的收款码 { token, amount, item, url }
    busy: false
  },

  onShow() { this.load() },

  load() {
    api.collections().then(r => {
      this.setData({
        list: (r && r.collections) || [],
        pending: (r && r.pending_count) || 0
      })
    }).catch(err => api.reportError(err))
  },

  onAmount(e) { this.setData({ amount: e.detail.value }) },
  onItem(e) { this.setData({ item: e.detail.value }) },

  // 弹层内部点击不穿透到遮罩（遮罩点击关闭）
  noop() {},

  // 生成收款码
  create() {
    const amt = parseFloat(this.data.amount)
    if (!amt || amt <= 0) {
      wx.showToast({ title: '请填写收款金额', icon: 'none' })
      return
    }
    this.setData({ busy: true })
    api.createCollection({ amount: amt, item: this.data.item || '现场收款' })
      .then(r => {
        const c = r.collection
        this.setData({
          busy: false,
          amount: '', item: '',
          qr: {
            token: c.token,
            amount: c.amount,
            item: c.item,
            // 二维码里必须是顾客手机能访问到的绝对地址
            img: api.collectionQrUrl(c.token) + '?origin=' +
              encodeURIComponent(api.getBaseUrl()),
            pay: api.payPageUrl(c.token)
          }
        })
        this.load()
      }).catch(err => {
        this.setData({ busy: false })
        api.reportError(err)
      })
  },

  closeQr() { this.setData({ qr: null }) },

  // 复制收款链接（发微信/短信给熟客用）
  copyLink() {
    if (!this.data.qr) return
    wx.setClipboardData({
      data: this.data.qr.pay,
      success: () => wx.showToast({ title: '链接已复制', icon: 'none' })
    })
  },

  openLink() {
    if (!this.data.qr) return
    wx.setClipboardData({
      data: this.data.qr.pay,
      success: () => wx.showModal({
        title: '收款链接已复制',
        content: '演示时可在浏览器打开该链接，模拟顾客点「我已付款」。',
        showCancel: false
      })
    })
  },

  // 确认入账
  confirm(e) {
    const id = Number(e.currentTarget.dataset.id)
    api.confirmCollection(id).then(r => {
      const amt = (r && (r.amount || (r.collection && r.collection.amount))) || ''
      wx.showToast({ title: '已入账 ' + amt + ' 元', icon: 'success' })
      this.load()
    }).catch(api.reportError)
  },

  cancel(e) {
    const id = Number(e.currentTarget.dataset.id)
    wx.showModal({
      title: '取消收款',
      editable: true,
      placeholderText: '原因（可留空）',
      success: res => {
        if (!res.confirm) return
        api.cancelCollection(id, res.content || '').then(() => {
          wx.showToast({ title: '已取消', icon: 'none' })
          this.load()
        }).catch(api.reportError)
      }
    })
  },

  copyToken(e) {
    const token = e.currentTarget.dataset.token
    wx.setClipboardData({
      data: api.payPageUrl(token),
      success: () => wx.showToast({ title: '收款链接已复制', icon: 'none' })
    })
  }
})
