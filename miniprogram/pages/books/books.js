// pages/books/books.js 账本：流水 / 算税 / 科目 / 报表（省账通能力）
const api = require('../../utils/api')

function pad(n) { return n < 10 ? '0' + n : '' + n }

Page({
  data: {
    tabIndex: 0,
    tabs: ['流水', '算税', '科目', '报表'],
    // ---- 流水 ----
    year: 0,
    month: 0,
    transactions: [],
    txnLoading: true,
    // ---- 算税 ----
    vatRevenue: '',
    vatResult: null,
    surtaxResult: null,
    pitSalary: '',
    pitSocial: '',
    pitSpecial: '',
    pitResult: null,
    citIncome: '',
    citSmall: true,
    citResult: null,
    calendar: null,
    // ---- 科目 ----
    accounts: [],
    // ---- 报表 ----
    downloading: false
  },

  onLoad() {
    const now = new Date()
    this.setData({ year: now.getFullYear(), month: now.getMonth() + 1 })
    this.loadTransactions()
    api.accountTitles().then(r => this.setData({ categories: r.categories || [] })).catch(() => {})
  },

  onShow() {
    if (this.data.tabIndex === 0) this.loadTransactions()
  },

  switchTab(e) {
    const idx = Number(e.currentTarget.dataset.index)
    this.setData({ tabIndex: idx })
    if (idx === 0) this.loadTransactions()
    if (idx === 1 && !this.data.calendar) this.loadCalendar()
  },

  // ================= 流水 =================
  onMonthChange(e) {
    const v = e.detail.value // 'YYYY-MM'
    const [y, m] = v.split('-').map(Number)
    this.setData({ year: y, month: m })
    this.loadTransactions()
  },

  loadTransactions() {
    const { year, month } = this.data
    this.setData({ summaryLoading: true })
    Promise.all([api.transactions(year, month), api.monthlySummary()])
      .then(([txns]) => {
        this.setData({ transactions: txns, summaryLoading: false })
      })
      .catch(() => {
        this.setData({ transactions: [], summaryLoading: false })
        wx.showToast({ title: '加载流水失败，请确认后端已启动', icon: 'none' })
      })
  },

  // ================= 算税 =================
  loadCalendar() {
    api.taxCalendar(this.data.year, this.data.month)
      .then(cal => this.setData({ calendar: cal }))
      .catch(() => {})
  },

  onVatInput(e) { this.setData({ vatRevenue: e.detail.value }) },
  calcVat() {
    const v = parseFloat(this.data.vatRevenue)
    if (!v || v <= 0) { wx.showToast({ title: '先填季度销售额', icon: 'none' }); return }
    api.taxVat(v).then(r => {
      const surtax = r.vat > 0 ? r.vat : null
      this.setData({ vatResult: r })
      if (surtax) {
        api.taxSurtax(surtax).then(s => this.setData({ surtaxResult: s })).catch(() => {})
      } else {
        this.setData({ surtaxResult: null })
      }
    }).catch(() => wx.showToast({ title: '算税失败', icon: 'none' }))
  },

  onPitInput(e) {
    const key = e.currentTarget.dataset.key
    this.setData({ [key]: e.detail.value })
  },
  calcPit() {
    const salary = parseFloat(this.data.pitSalary)
    if (!salary || salary <= 0) { wx.showToast({ title: '先填月工资', icon: 'none' }); return }
    api.taxPit(salary, parseFloat(this.data.pitSocial) || 0, parseFloat(this.data.pitSpecial) || 0)
      .then(r => this.setData({ pitResult: r }))
      .catch(() => wx.showToast({ title: '算税失败', icon: 'none' }))
  },

  onCitInput(e) { this.setData({ citIncome: e.detail.value }) },
  toggleCitSmall(e) { this.setData({ citSmall: e.detail.value === '1' }) },
  calcCit() {
    const income = parseFloat(this.data.citIncome)
    if (!income || income <= 0) { wx.showToast({ title: '先填年应纳税所得额', icon: 'none' }); return }
    api.taxCit(income, this.data.citSmall)
      .then(r => this.setData({ citResult: r }))
      .catch(() => wx.showToast({ title: '算税失败', icon: 'none' }))
  },

  // ================= 报表 =================
  downloadReport() {
    if (this.data.downloading) return
    const { year, month } = this.data
    const url = api.reportUrl(year, month)
    this.setData({ downloading: true })
    wx.showLoading({ title: '正在生成报表…' })
    wx.downloadFile({
      url,
      timeout: 60000,
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode !== 200) {
          wx.showToast({ title: '报表生成失败', icon: 'none' })
          return
        }
        wx.openDocument({
          filePath: res.tempFilePath,
          fileType: 'xlsx',
          showMenu: true,
          fail: () => wx.showToast({ title: '文件已下载，但本机无法预览', icon: 'none' })
        })
      },
      fail: () => {
        wx.hideLoading()
        wx.showToast({ title: '下载失败，请确认后端已启动', icon: 'none' })
      },
      complete: () => this.setData({ downloading: false })
    })
  }
})