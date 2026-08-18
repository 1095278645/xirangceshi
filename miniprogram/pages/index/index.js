// pages/index/index.js 语音记账
const api = require('../../utils/api')
const app = getApp()

// 同声传译插件为可选能力：正式 AppID 授权后可用，否则降级为手动输入
let manager = null
try {
  const plugin = requirePlugin('WechatSI')
  manager = plugin.getRecordRecognitionManager()
} catch (e) {
  console.warn('[AI掌柜] 同声传译插件不可用（需正式 AppID 并授权），语音已降级为手动输入')
}

Page({
  data: {
    shopName: '我的小店',
    summary: { income: 0, expense: 0, balance: 0, cnt: 0 },
    month: { period: '', income: 0, expense: 0, balance: 0 },
    recognizing: false,
    voiceEnabled: !!manager,
    result: '',
    submitting: false,
    parsed: null,       // 解析结果
    voucher: null,      // 凭证信息
    friendlyCategory: '',
    manualText: ''
  },

  onLoad() {
    this.shopName = app.globalData.shopName
    this.setData({ shopName: this.shopName })
    this.initRecognizer()
    this.loadSummary()
    this.loadMonth()
  },

  onShow() {
    this.loadSummary()
    this.loadMonth()
  },

  loadSummary() {
    api.todaySummary().then(s => this.setData({ summary: s })).catch(() => {})
  },

  loadMonth() {
    api.monthlySummary().then(m => this.setData({ month: m })).catch(() => {})
  },

  // 初始化语音识别（微信同声传译插件，可选）
  initRecognizer() {
    if (!manager) return
    manager.onRecognize = (res) => {
      this.setData({ result: res.result })
    }
    manager.onStop = (res) => {
      this.setData({ recognizing: false })
      if (res.result) {
        this.submitOrder(res.result)
      } else {
        wx.showToast({ title: '没听清，再说一遍？', icon: 'none' })
      }
    }
    manager.onError = () => {
      this.setData({ recognizing: false })
      wx.showToast({ title: '语音识别失败，请手动输入', icon: 'none' })
    }
  },

  // 按住说话
  startRecord() {
    if (!manager) {
      wx.showToast({ title: '语音需正式AppID授权，请用下方手动输入', icon: 'none' })
      return
    }
    this.setData({ recognizing: true, result: '', parsed: null })
    manager.start({ lang: 'zh_CN', duration: 10000 })
  },

  endRecord() {
    if (!manager) return
    manager.stop()
  },

  cancelRecord() {
    if (!manager) return
    manager.stop()
    this.setData({ recognizing: false })
  },

  // 提交记账
  submitOrder(text) {
    this.setData({ submitting: true })
    api.createOrder(text)
      .then(res => {
        // WXML 不支持方法调用：tags 在 JS 层拆成数组
        const parsed = res.parsed
        if (parsed && parsed.tags) {
          parsed.tagsArr = String(parsed.tags).split(',').filter(Boolean)
        }
        this.setData({
          parsed,
          voucher: res.voucher,
          friendlyCategory: res.friendly_category,
          submitting: false,
          summary: res.summary,
          manualText: ''
        })
        this.loadMonth()
        if (res.amount_missing) {
          wx.showToast({ title: '金额没听清，只记了流水', icon: 'none' })
        } else if (res.customer_new) {
          wx.showToast({ title: '新熟客已记住', icon: 'none' })
        }
      })
      .catch(err => {
        this.setData({ submitting: false })
        wx.showToast({ title: err.message, icon: 'none' })
      })
  },

  onManualInput(e) {
    this.setData({ manualText: e.detail.value })
  },

  submitManual() {
    const text = this.data.manualText.trim()
    if (!text) {
      wx.showToast({ title: '说点啥呢', icon: 'none' })
      return
    }
    this.submitOrder(text)
  },

  goMemory() {
    wx.reLaunch({ url: '/pages/memory/memory' })
  },

  goCopy() {
    wx.reLaunch({ url: '/pages/copy/copy' })
  }
})