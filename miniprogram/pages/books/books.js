// pages/books/books.js 账本：流水 / 算税 / 科目 / 报表 / 现金流 / 库存 / 发票
const api = require('../../utils/api')

function pad(n) { return n < 10 ? '0' + n : '' + n }

function todayStr() {
  const n = new Date()
  return `${n.getFullYear()}-${pad(n.getMonth() + 1)}-${pad(n.getDate())}`
}

Page({
  data: {
    tabIndex: 0,
    tabs: ['流水', '算税', '科目', '报表'],
    apiProfile: 'core',
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
    taxAdviceCached: false,
    // ---- 资金健康：现金流预测 / 预算 / 应收应付 ----
    finTabIndex: 0,
    finTabs: ['现金流', '预算', '赊账'],
    cashOnHand: '',
    cashMonths: '6',
    cashResult: null,
    cashLoading: false,
    budMonth: '',
    budgets: [],
    budVs: null,
    budScope: 'expense',
    budAmount: '',
    budCategory: '',
    debts: [],
    aging: null,
    debtParty: '',
    debtKind: 'receivable',
    debtAmount: '',
    debtDue: '',
    // ---- 库存 ----
    stockSummary: null,
    stockLoading: false,
    prodName: '',
    prodUnit: '',
    prodQty: '',
    prodSafety: '',
    prodCost: '',
    prodExpiry: '',
    // ---- 发票 ----
    invoiceSummary: null,
    invoiceLoading: false,
    invKind: 'out',
    invParty: '',
    invNo: '',
    invAmount: '',
    invRate: '1',
    invDate: ''
  },

  onLoad() {
    const now = new Date()
    this.setData({ year: now.getFullYear(), month: now.getMonth() + 1 })
    api.resetFailFlag()
    api.getSettings()
      .then(s => this.setData({
        apiProfile: s.api_profile || 'core',
        tabs: (s.api_profile || 'core') === 'full'
          ? ['流水', '算税', '科目', '报表', '现金', '库存', '发票']
          : ['流水', '算税', '科目', '报表']
      }))
      .catch(() => null)
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
    if (this.data.apiProfile !== 'full' && idx > 3) return
    this.setData({ tabIndex: idx })
    if (idx === 0) this.loadTransactions()
    if (idx === 1 && !this.data.calendar) this.loadCalendar()
    if (idx === 4 && !this.data.budMonth) {
      const now = new Date()
      this.setData({ budMonth: `${now.getFullYear()}-${pad(now.getMonth() + 1)}` })
    }
    if (idx === 5 && !this.data.stockSummary) this.loadStock()
    if (idx === 6 && !this.data.invoiceSummary) this.loadInvoice()
  },

  // ================= 资金健康：现金流 / 预算 / 赊账 =================
  switchFinTab(e) {
    const idx = Number(e.currentTarget.dataset.index)
    this.setData({ finTabIndex: idx })
    if (idx === 1) this.loadBudgets()
    if (idx === 2) this.loadDebts()
  },

  onCashInput(e) { this.setData({ cashOnHand: e.detail.value }) },
  onCashMonthsInput(e) { this.setData({ cashMonths: e.detail.value }) },

  runCashflow() {
    const cash = parseFloat(this.data.cashOnHand)
    if (!(cash >= 0)) { wx.showToast({ title: '先填现有现金', icon: 'none' }); return }
    this.setData({ cashLoading: true, cashResult: null })
    api.cashflow({ cash_on_hand: cash, months: parseInt(this.data.cashMonths) || 6 })
      .then(r => this.setData({ cashResult: r, cashLoading: false }))
      .catch(err => { this.setData({ cashLoading: false }); api.reportError(err) })
  },

  loadBudgets() {
    const m = this.data.budMonth
    Promise.all([api.budgets(m), api.budgetActual(m)])
      .then(([budgets, vs]) => {
        // 后端返回可能是数组或 {items:[...]}，统一成数组，避免 wxml 读 .length 崩
        const list = Array.isArray(budgets) ? budgets : (budgets.items || [])
        this.setData({ budgets: list, budVs: vs })
      })
      .catch(err => api.reportError(err))
  },

  onBudMonthChange(e) {
    this.setData({ budMonth: e.detail.value })
    this.loadBudgets()
  },
  setBudScope(e) { this.setData({ budScope: e.currentTarget.dataset.scope }) },
  onBudAmountInput(e) { this.setData({ budAmount: e.detail.value }) },
  onBudCategoryInput(e) { this.setData({ budCategory: e.detail.value }) },

  saveBudget() {
    const amount = parseFloat(this.data.budAmount)
    if (!(amount >= 0)) { wx.showToast({ title: '先填预算金额', icon: 'none' }); return }
    api.saveBudget({ month: this.data.budMonth, scope: this.data.budScope, amount,
                     category: this.data.budCategory.trim(), note: '' })
      .then(() => {
        this.setData({ budAmount: '', budCategory: '' })
        wx.showToast({ title: '预算已保存', icon: 'success' })
        this.loadBudgets()
      })
      .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
  },

  deleteBudget(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '删除这条预算？', content: '删除后不影响已有流水',
      success: (r) => {
        if (!r.confirm) return
        api.deleteBudget(id).then(() => this.loadBudgets())
          .catch(err => api.reportError(err))
      }
    })
  },

  loadDebts() {
    Promise.all([api.debts(), api.debtsAging()])
      .then(([debts, aging]) => {
        const list = Array.isArray(debts) ? debts : (debts.items || [])
        this.setData({ debts: list, aging })
      })
      .catch(err => api.reportError(err))
  },

  setDebtKind(e) { this.setData({ debtKind: e.currentTarget.dataset.kind }) },
  onDebtPartyInput(e) { this.setData({ debtParty: e.detail.value }) },
  onDebtAmountInput(e) { this.setData({ debtAmount: e.detail.value }) },
  onDebtDueChange(e) { this.setData({ debtDue: e.detail.value }) },

  addDebt() {
    const amount = parseFloat(this.data.debtAmount)
    if (!this.data.debtParty.trim()) { wx.showToast({ title: '填一下对方是谁', icon: 'none' }); return }
    if (!(amount > 0)) { wx.showToast({ title: '先填金额', icon: 'none' }); return }
    api.addDebt({ party: this.data.debtParty.trim(), kind: this.data.debtKind,
                  amount, due_date: this.data.debtDue || '', note: '' })
      .then(() => {
        this.setData({ debtParty: '', debtAmount: '', debtDue: '' })
        wx.showToast({ title: '已记下这笔账', icon: 'success' })
        this.loadDebts()
      })
      .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
  },

  settleDebt(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '结清这笔账？', content: '表示这笔赊账已全部收到/付清',
      success: (r) => {
        if (!r.confirm) return
        api.settleDebt(id).then(() => this.loadDebts()).catch(err => api.reportError(err))
      }
    })
  },

  deleteDebt(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '删除这笔账？', content: '', success: (r) => {
        if (!r.confirm) return
        api.deleteDebt(id).then(() => this.loadDebts()).catch(err => api.reportError(err))
      }
    })
  },

  // ================= 库存 =================
  loadStock() {
    this.setData({ stockLoading: true })
    api.stock()
      .then(r => this.setData({ stockSummary: r, stockLoading: false }))
      .catch(err => { this.setData({ stockLoading: false }); api.reportError(err) })
  },

  onProdInput(e) {
    this.setData({ [e.currentTarget.dataset.key]: e.detail.value })
  },
  onProdExpiryChange(e) { this.setData({ prodExpiry: e.detail.value }) },

  saveProduct() {
    const f = this.data
    if (!String(f.prodName || '').trim()) {
      wx.showToast({ title: '请填商品/材料名', icon: 'none' }); return
    }
    api.saveProduct({
      name: String(f.prodName).trim(), category: '', unit: f.prodUnit || '',
      stock_qty: parseFloat(f.prodQty) || 0,
      safety_stock: parseFloat(f.prodSafety) || 0,
      unit_cost: parseFloat(f.prodCost) || 0,
      expiry_date: f.prodExpiry || '', supplier: '', note: ''
    }).then(() => {
      this.setData({ prodName: '', prodUnit: '', prodQty: '',
                     prodSafety: '', prodCost: '', prodExpiry: '' })
      wx.showToast({ title: '已登记入库', icon: 'success' })
      this.loadStock()
    }).catch(err => wx.showToast({ title: err.message, icon: 'none' }))
  },

  // 入库(in)/出库(out)/盘点(adj)：小程序没有 prompt，用 modal 的 editable 输入
  promptMove(e) {
    const { id, name, act } = e.currentTarget.dataset
    const label = { in: '入库', out: '出库', adj: '盘点' }[act]
    wx.showModal({
      title: `${label}：${name}`,
      editable: true,
      placeholderText: act === 'adj' ? '盘点后的实际库存数量' : '数量',
      success: (r) => {
        if (!r.confirm) return
        const qty = parseFloat(r.content)
        if (isNaN(qty) || qty < 0) { wx.showToast({ title: '数量不对', icon: 'none' }); return }
        api.moveStock(id, act, qty)
          .then(() => { wx.showToast({ title: '库存已更新', icon: 'success' }); this.loadStock() })
          .catch(err => api.reportError(err))
      }
    })
  },

  deleteProduct(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '删除这个商品档案？', content: '库存流水会一并删除',
      success: (r) => {
        if (!r.confirm) return
        api.deleteProduct(id).then(() => this.loadStock()).catch(err => api.reportError(err))
      }
    })
  },

  // ================= 发票 =================
  loadInvoice() {
    this.setData({ invoiceLoading: true })
    api.invoiceSummary()
      .then(r => this.setData({ invoiceSummary: r, invoiceLoading: false }))
      .catch(err => { this.setData({ invoiceLoading: false }); api.reportError(err) })
  },

  setInvKind(e) { this.setData({ invKind: e.currentTarget.dataset.kind }) },
  onInvInput(e) { this.setData({ [e.currentTarget.dataset.key]: e.detail.value }) },
  onInvDateChange(e) { this.setData({ invDate: e.detail.value }) },

  saveInvoice() {
    const f = this.data
    const amount = parseFloat(f.invAmount)
    if (!(amount > 0)) { wx.showToast({ title: '先填开票/收票金额', icon: 'none' }); return }
    const ratePct = parseFloat(f.invRate)
    const rate = isNaN(ratePct) ? 0 : ratePct / 100     // 用户填 %，后端存小数
    api.saveInvoice({
      kind: f.invKind, party: String(f.invParty || '').trim(),
      invoice_no: String(f.invNo || '').trim(), amount, rate,
      tax_amount: Math.round(amount * rate * 100) / 100,
      issued_date: f.invDate || todayStr(), note: ''
    }).then(() => {
      this.setData({ invParty: '', invNo: '', invAmount: '', invDate: '' })
      wx.showToast({ title: '发票已记入台账', icon: 'success' })
      this.loadInvoice()
    }).catch(err => wx.showToast({ title: err.message, icon: 'none' }))
  },

  voidInvoice(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '作废这张发票？', content: '', success: (r) => {
        if (!r.confirm) return
        api.voidInvoice(id).then(() => this.loadInvoice()).catch(err => api.reportError(err))
      }
    })
  },

  // ================= 流水更正（改错账 / 作废 / 退货冲销） =================
  //
  // 为什么要有这套：小店里记错金额、记错类型、卖出去又退回来都很常见。
  // 早期只能删库改数据，会计上完全说不通。现在三种操作都会写审计记录，
  // 作废/退货也不会物理删除原凭证（红冲），账目可追溯。
  onTxnTap(e) {
    const { id, item, amount, type } = e.currentTarget.dataset
    wx.showActionSheet({
      itemList: ['修改金额/品名', '作废这笔', '退货冲销'],
      success: res => {
        if (res.tapIndex === 0) this.editTxn(id, item, amount, type)
        else if (res.tapIndex === 1) this.voidTxn(id, item)
        else if (res.tapIndex === 2) this.refundTxn(id, item, amount)
      }
    })
  },
  editTxn(id, item, amount) {
    wx.showModal({
      title: '修改金额',
      editable: true,
      placeholderText: '新的金额',
      content: String(amount),
      success: res => {
        if (!res.confirm) return
        const val = parseFloat(res.content)
        if (isNaN(val) || val < 0) {
          wx.showToast({ title: '请填写正确的金额', icon: 'none' })
          return
        }
        wx.showModal({
          title: '修改原因',
          editable: true,
          placeholderText: '如：当时记错了（可留空）',
          success: r2 => {
            if (!r2.confirm) return
            // 只提交要改的字段：品名/分类/方向都不动，
            // 避免"改金额"顺手把分类改掉导致报表串科目。
            api.editTransaction(Number(id), {
              amount: val, reason: r2.content || ''
            }).then(() => {
              wx.showToast({ title: '已更正，留痕可查', icon: 'none' })
              this.loadTransactions()
            }).catch(api.reportError)
          }
        })
      }
    })
  },

  voidTxn(id, item) {
    wx.showModal({
      title: '作废这笔账',
      content: `「${item}」将不被计入统计（原凭证保留，可追溯）。确认作废？`,
      confirmColor: '#b4532a',
      editable: true,
      placeholderText: '作废原因（可留空）',
      success: res => {
        if (!res.confirm) return
        api.voidTransaction(Number(id), res.content || '')
          .then(() => {
            wx.showToast({ title: '已作废', icon: 'none' })
            this.loadTransactions()
          }).catch(api.reportError)
      }
    })
  },

  refundTxn(id, item, amount) {
    wx.showModal({
      title: '退货冲销',
      editable: true,
      placeholderText: '退款金额（默认全额 ' + amount + '）',
      content: String(amount),
      success: res => {
        if (!res.confirm) return
        const val = parseFloat(res.content)
        if (!val || val <= 0) {
          wx.showToast({ title: '退款金额要大于 0', icon: 'none' })
          return
        }
        api.refundTransaction(Number(id), {
          amount: val, reason: '顾客退货'
        }).then(() => {
          wx.showToast({ title: '已冲销 ' + val + ' 元', icon: 'none' })
          this.loadTransactions()
        }).catch(api.reportError)
      }
    })
  },

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
