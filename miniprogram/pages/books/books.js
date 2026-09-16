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
    categories: [],
    // ---- 报表 ----
    downloading: false,
    // ---- AI 经营洞察 ----
    insight: '',
    insightLoading: false,
    insightAiUsed: false,
    insightCached: false,
    // ---- AI 报税建议 ----
    taxAdvice: '',
    taxAdviceLoading: false,
    taxAdviceAiUsed: false,
    taxAdviceCached: false
  },

  onLoad() {
    const now = new Date()
    this.setData({ year: now.getFullYear(), month: now.getMonth() + 1 })
    api.resetFailFlag()
    this.loadTransactions()
    api.accountTitles()
      .then(r => this.setData({ categories: r.categories || [] }))
      .catch(err => api.reportError(err))
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
        // 洞察按月缓存：命中缓存时后端秒回，只有首次或手动刷新才调 AI
        // （原实现每次进页面/切标签都会触发 20~30 秒的 AI 调用）
        this.loadInsights()
      })
      .catch(() => {
        this.setData({ transactions: [], summaryLoading: false })
        wx.showToast({ title: '加载流水失败，请确认后端已启动', icon: 'none' })
      })
  },

  loadInsights(refresh) {
    const { year, month } = this.data
    this.setData({
      insightLoading: true,
      insight: refresh ? '' : this.data.insight,
      insightCached: !refresh,
    })
    api.orderInsights(year, month, !!refresh)
      .then(r => {
        this.setData({
          insight: r.insights,
          insightAiUsed: r.ai_used,
          insightCached: !!r.cached,
          insightLoading: false,
        })
      })
      .catch(err => {
        this.setData({ insightLoading: false })
        api.reportError(err)
      })
  },

  // 手动重新分析（会调用 AI，约 5~30 秒）
  refreshInsights() {
    if (this.data.insightLoading) return
    this.loadInsights(true)
  },

  // ================= 算税 =================
  loadCalendar() {
    api.taxCalendar(this.data.year, this.data.month)
      .then(cal => this.setData({ calendar: cal }))
      .catch(err => api.reportError(err))
  },

  onVatInput(e) { this.setData({ vatRevenue: e.detail.value }) },
  calcVat() {
    const v = parseFloat(this.data.vatRevenue)
    if (!v || v <= 0) { wx.showToast({ title: '先填季度销售额', icon: 'none' }); return }
    api.taxVat(v).then(r => {
      const surtax = r.vat > 0 ? r.vat : null
      this.setData({ vatResult: r })
      if (surtax) {
        api.taxSurtax(surtax)
          .then(s => this.setData({ surtaxResult: s }))
          .catch(err => api.reportError(err))
      } else {
        this.setData({ surtaxResult: null })
      }
      this.loadTaxAdvice()
    }).catch(err => api.reportError(err))
  },

  loadTaxAdvice(refresh) {
    const v = parseFloat(this.data.vatRevenue)
    if (!v || v <= 0) return
    this.setData({
      taxAdviceLoading: true,
      taxAdvice: refresh ? '' : this.data.taxAdvice,
    })
    api.taxAdvice(v, !!refresh)
      .then(r => {
        this.setData({
          taxAdvice: r.advice,
          taxAdviceAiUsed: r.ai_used,
          taxAdviceCached: !!r.cached,
          taxAdviceLoading: false,
        })
      })
      .catch(err => {
        this.setData({ taxAdviceLoading: false })
        api.reportError(err)
      })
  },

  // 手动重新生成报税建议（会调 AI，约 30~65 秒）
  refreshTaxAdvice() {
    if (this.data.taxAdviceLoading) return
    this.loadTaxAdvice(true)
  },

  onPitInput(e) {
    const key = e.currentTarget.dataset.key
    this.setData({ [key]: e.detail.value })
  },
  calcPit() {
    const salary = parseFloat(this.data.pitSalary)
    if (!salary || salary <= 0) { wx.showToast({ title: '先填月工资', icon: 'none' }); return }
    api.taxPit(salary, parseFloat(this.data.pitSocial) || 0, parseFloat(this.data.pitSpecial) || 0)
      .then(r => {
        // 字段兜底：后端缺 rate 等字段时避免 WXML 显示 NaN
        this.setData({ pitResult: {
          taxable: r.taxable || 0, tax: r.tax || 0,
          rate: r.rate || 0, quick_deduction: r.quick_deduction || 0
        } })
      })
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
      // 报表接口同样受鉴权保护：带上令牌头（后端也支持 ?token= 兜底）
      header: api.authHeader(),
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode === 401) {
          wx.showToast({ title: '需要访问令牌，请到「设置」填写', icon: 'none' })
          return
        }
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