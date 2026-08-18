// pages/settings/settings.js 设置：AI 模型 + 收款账户（二维码流水同步）
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
    keyLabel: 'API Key（sk- 开头）',
    // 收款账户
    paySources: [],
    payLogs: [],
    payTypeIndex: 0,
    payTypes: ['微信支付商户号', '聚合支付（无执照）'],
    payName: '',
    payMchid: '',
    payAppid: '',
    payCertPath: '',
    payPrivKeyPath: '',
    payV3Key: '',
    payEnabled: true,
    paySyncing: false
  },

  onLoad() {
    this.load()
  },

  onShow() {
    this.load()
  },

  load() {
    Promise.all([api.getSettings(), api.getProviders(), api.paySources(), api.payLogs()])
      .then(([s, p, ps, pl]) => {
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
          keyLabel: cur.key_label || 'API Key',
          paySources: ps.sources || [],
          payLogs: pl.logs || []
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
  },

  // ---------- 收款账户 ----------
  onPayTypeChange(e) {
    this.setData({ payTypeIndex: Number(e.detail.value) })
  },

  onPayNameInput(e) { this.setData({ payName: e.detail.value }) },
  onPayMchidInput(e) { this.setData({ payMchid: e.detail.value }) },
  onPayAppidInput(e) { this.setData({ payAppid: e.detail.value }) },
  onPayCertInput(e) { this.setData({ payCertPath: e.detail.value }) },
  onPayPrivKeyInput(e) { this.setData({ payPrivKeyPath: e.detail.value }) },
  onPayV3KeyInput(e) { this.setData({ payV3Key: e.detail.value }) },
  onPayEnabledChange(e) { this.setData({ payEnabled: e.detail.value }) },

  savePaySource() {
    const name = (this.data.payName || '').trim()
    const mchid = (this.data.payMchid || '').trim()
    if (!name) { wx.showToast({ title: '请填写账户名称', icon: 'none' }); return }
    if (!mchid) { wx.showToast({ title: '请填写商户号，无资料可填 DEMO', icon: 'none' }); return }
    const source_type = this.data.payTypeIndex === 0 ? 'wechat' : 'aggregate'
    api.savePaySource({
      source_type,
      name,
      mchid,
      appid: (this.data.payAppid || '').trim(),
      cert_path: (this.data.payCertPath || '').trim(),
      private_key_path: (this.data.payPrivKeyPath || '').trim(),
      api_v3_key: (this.data.payV3Key || '').trim(),
      enabled: this.data.payEnabled
    })
      .then(() => {
        wx.showToast({ title: '已保存', icon: 'success' })
        this.setData({ payName: '', payMchid: '', payAppid: '', payCertPath: '', payPrivKeyPath: '', payV3Key: '' })
        this.load()
      })
      .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
  },

  deletePaySource(e) {
    const id = e.currentTarget.dataset.id
    const name = e.currentTarget.dataset.name
    wx.showModal({
      title: '删除收款账户？',
      content: `删除「${name}」？已同步的流水不受影响。`,
      success: (res) => {
        if (!res.confirm) return
        api.deletePaySource(id)
          .then(() => {
            wx.showToast({ title: '已删除', icon: 'none' })
            this.load()
          })
          .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
      }
    })
  },

  syncPaySource(e) {
    const id = e.currentTarget.dataset.id
    this.setData({ paySyncing: true })
    api.syncPaySource(id)
      .then(r => {
        if (r.ok) wx.showToast({ title: `同步完成：新增 ${r.imported} 笔`, icon: 'none' })
        else wx.showToast({ title: '同步失败：' + r.error, icon: 'none' })
      })
      .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
      .finally(() => {
        this.setData({ paySyncing: false })
        this.load()
      })
  },

  syncAllPay() {
    this.setData({ paySyncing: true })
    api.syncAllPay()
      .then(r => wx.showToast({ title: `已触发全部账户同步（${r.length} 个）`, icon: 'none' }))
      .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
      .finally(() => {
        this.setData({ paySyncing: false })
        this.load()
      })
  },

  clearDemoPay() {
    wx.showModal({
      title: '清空演示流水？',
      content: '将清空所有演示模式（DEMO-）产生的流水，确定？',
      success: (res) => {
        if (!res.confirm) return
        api.demoClear()
          .then(r => wx.showToast({ title: `已清空 ${r.deleted} 条`, icon: 'none' }))
          .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
          .finally(() => this.load())
      }
    })
  }
})