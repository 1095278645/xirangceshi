// pages/copy/copy.js 朋友圈文案生成
const api = require('../../utils/api')
const app = getApp()

Page({
  data: {
    shopName: '',
    sceneIndex: 0,
    scenes: ['今日营业', '上新推荐', '优惠活动', '节日问候', '日常碎碎念'],
    extra: '',
    customerName: '',
    result: '',
    generating: false
  },

  onLoad() {
    this.setData({ shopName: app.globalData.shopName })
  },

  onShopInput(e) {
    this.setData({ shopName: e.detail.value })
  },

  onSceneChange(e) {
    this.setData({ sceneIndex: Number(e.detail.value) })
  },

  onExtraInput(e) {
    this.setData({ extra: e.detail.value })
  },

  onCustomerInput(e) {
    this.setData({ customerName: e.detail.value })
  },

  generate() {
    if (!this.data.shopName.trim()) {
      wx.showToast({ title: '先填个店名吧', icon: 'none' })
      return
    }
    this.setData({ generating: true })
    api.generateCopy({
      shop_name: this.data.shopName,
      scene: this.data.scenes[this.data.sceneIndex],
      extra: this.data.extra,
      customer_name: this.data.customerName
    }).then(res => {
      this.setData({ result: res.text, generating: false })
    }).catch(err => {
      this.setData({ generating: false })
      wx.showToast({ title: err.message, icon: 'none' })
    })
  },

  copyResult() {
    if (!this.data.result) return
    wx.setClipboardData({
      data: this.data.result,
      success: () => wx.showToast({ title: '已复制，去发朋友圈吧', icon: 'none' })
    })
  }
})