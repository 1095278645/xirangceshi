// pages/index/index.js 语音记账
const api = require('../../utils/api')
const app = getApp()
const plugin = requirePlugin('WechatSI')
const manager = plugin.getRecordRecognitionManager()

Page({
  data: {
    shopName: '我的小店',
    summary: { total: 0, cnt: 0 },
    recognizing: false,
    voiceText: '',
    submitting: false,
    parsed: null,       // 解析结果
    manualText: '',
    remindTip: ''
  },

  onLoad() {
    this.shopName = app.globalData.shopName
    this.setData({ shopName: this.shopName })
    this.initRecognizer()
    this.loadSummary()
  },

  onShow() {
    this.loadSummary()
  },

  loadSummary() {
    api.todaySummary().then(s => this.setData({ summary: s })).catch(() => {})
  },

  // 初始化语音识别（微信同声传译插件）
  initRecognizer() {
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
    this.setData({ recognizing: true, result: '', parsed: null })
    manager.start({ lang: 'zh_CN', duration: 10000 })
  },

  endRecord() {
    manager.stop()
  },

  cancelRecord() {
    manager.stop()
    this.setData({ recognizing: false })
  },

  // 提交记账
  submitOrder(text) {
    this.setData({ submitting: true })
    api.createOrder(text)
      .then(res => {
        const parsed = res.parsed
        this.setData({
          parsed: parsed,
          submitting: false,
          summary: res.summary,
          manualText: ''
        })
        if (res.customer_new) {
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