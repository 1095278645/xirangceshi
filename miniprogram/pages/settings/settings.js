// pages/settings/settings.js 设置：填入自己的 DeepSeek API Key
const api = require('../../utils/api')

Page({
  data: {
    aiEnabled: false,
    hasKey: false,
    baseUrl: '',
    model: '',
    apiKey: '',
    saving: false
  },

  onLoad() {
    this.load()
  },

  onShow() {
    this.load()
  },

  load() {
    api.getSettings()
      .then(s => this.setData({
        aiEnabled: s.ai_enabled,
        hasKey: s.has_key,
        baseUrl: s.base_url,
        model: s.model
      }))
      .catch(() => {})
  },

  onApiInput(e) {
    this.setData({ apiKey: e.detail.value })
  },

  onBaseUrlInput(e) {
    this.setData({ baseUrl: e.detail.value })
  },

  onModelInput(e) {
    this.setData({ model: e.detail.value })
  },

  save() {
    const key = this.data.apiKey.trim()
    if (!key) {
      wx.showToast({ title: '请先粘贴 API Key', icon: 'none' })
      return
    }
    this.setData({ saving: true })
    api.saveSettings({
      api_key: key,
      base_url: this.data.baseUrl.trim(),
      model: this.data.model.trim()
    })
      .then(() => {
        wx.showToast({ title: '已保存，AI 生效', icon: 'success' })
        this.setData({ apiKey: '' })
        this.load()
      })
      .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
      .finally(() => this.setData({ saving: false }))
  },

  clearKey() {
    wx.showModal({
      title: '清除 API Key？',
      content: '清除后将回到兜底模式（手动记账仍可用，AI 功能关闭）',
      success: (res) => {
        if (!res.confirm) return
        api.saveSettings({ api_key: '' })
          .then(() => {
            wx.showToast({ title: '已清除', icon: 'none' })
            this.load()
          })
          .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
      }
    })
  },

  // 顺手提供获取 Key 的指引
  howToGet() {
    wx.setClipboardData({
      data: 'https://platform.deepseek.com',
      success: () => wx.showToast({ title: '链接已复制，浏览器打开', icon: 'none' })
    })
  }
})