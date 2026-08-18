// pages/settings/settings.js 设置：选大模型 + 填 API Key
const api = require('../../utils/api')

Page({
  data: {
    aiEnabled: false,
    hasKey: false,
    baseUrl: '',
    model: '',
    apiKey: '',
    saving: false,
    providers: [],
    providerNames: [],
    providerIndex: 0,
    keyLabel: 'API Key（sk- 开头）'
  },

  onLoad() {
    this.load()
  },

  onShow() {
    this.load()
  },

  load() {
    Promise.all([api.getSettings(), api.getProviders()])
      .then(([s, p]) => {
        const providers = p.providers || []
        const providerNames = providers.map(x => x.name)
        let idx = providers.findIndex(x => x.id === s.provider)
        if (idx < 0) idx = providers.length - 1 // 自定义
        const cur = providers[idx] || {}
        this.setData({
          aiEnabled: s.ai_enabled,
          hasKey: s.has_key,
          baseUrl: s.base_url,
          model: s.model,
          providers,
          providerNames,
          providerIndex: idx,
          keyLabel: cur.key_label || 'API Key'
        })
      })
      .catch(() => {})
  },

  onProviderChange(e) {
    const idx = Number(e.detail.value)
    const p = this.data.providers[idx]
    if (!p) return
    this.setData({
      providerIndex: idx,
      keyLabel: p.key_label || 'API Key',
      baseUrl: p.base_url || this.data.baseUrl,
      model: p.model || this.data.model
    })
  },

  onApiKeyInput(e) {
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

  howToGet() {
    const p = this.data.providers[this.data.providerIndex]
    const url = (p && p.key_url) || ''
    if (!url) {
      wx.showToast({ title: '自定义服务请到对应平台获取 Key', icon: 'none' })
      return
    }
    wx.setClipboardData({
      data: url,
      success: () => wx.showToast({ title: '链接已复制，浏览器打开', icon: 'none' })
    })
  }
})