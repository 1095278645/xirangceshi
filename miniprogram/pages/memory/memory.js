// pages/memory/memory.js 熟客记忆
const api = require('../../utils/api')

Page({
  data: {
    customers: [],
    loading: true,
    detail: null,        // 当前查看的熟客
    newMemory: '',
    memories: []         // 今日提醒
  },

  onShow() {
    this.loadCustomers()
    this.loadReminders()
  },

  loadCustomers() {
    api.customers().then(list => {
      this.setData({ customers: list, loading: false })
    }).catch(() => {
      this.setData({ loading: false })
      wx.showToast({ title: '加载失败，请确认后端已启动', icon: 'none' })
    })
  },

  loadReminders() {
    api.reminders().then(list => {
      this.setData({ memories: list.filter(r => !r.done) })
    }).catch(() => {})
  },

  openDetail(e) {
    const id = e.currentTarget.dataset.id
    api.customerDetail(id).then(d => {
      this.setData({ detail: d })
    })
  },

  closeDetail() {
    this.setData({ detail: null })
  },

  onMemoryInput(e) {
    this.setData({ newMemory: e.detail.value })
  },

  saveMemory() {
    const content = this.data.newMemory.trim()
    if (!content || !this.data.detail) return
    api.addMemory(this.data.detail.id, content).then(() => {
      this.setData({ newMemory: '' })
      this.openDetail({ currentTarget: { dataset: { id: this.data.detail.id } } })
      wx.showToast({ title: '记住啦', icon: 'success' })
    })
  },

  // 一键生成今日提醒
  generateReminders() {
    wx.showLoading({ title: 'AI正在想…' })
    api.generateReminders().then(res => {
      wx.hideLoading()
      this.loadReminders()
      wx.showToast({ title: '已生成' + (res.reminders || []).length + '条提醒', icon: 'none' })
    }).catch(() => {
      wx.hideLoading()
      wx.showToast({ title: '生成失败，请检查API Key', icon: 'none' })
    })
  },

  doneReminder(e) {
    const id = e.currentTarget.dataset.id
    api.reminderDone(id).then(() => this.loadReminders())
  }
})