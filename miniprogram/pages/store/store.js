// pages/store/store.js 单店模型：保本线先行（勇哥方法论泛化）
const api = require('../../utils/api')

// 兜底预设：后端未启动时页面也能用（与 server/store.py 保持一致）
const FALLBACK_PRESETS = [
  { key: '餐饮', name: '餐饮（快餐/面馆/早餐）', margin_range: [0.50, 0.65], margin_default: 0.58, note: '餐饮的核心是翻台率和出餐效率，房租占比别超营业额 15%' },
  { key: '饮品', name: '茶饮/咖啡/甜品', margin_range: [0.55, 0.70], margin_default: 0.62, note: '饮品毛利高但极度依赖客流，选址=生死线' },
  { key: '零售', name: '便利店/超市/杂货', margin_range: [0.18, 0.30], margin_default: 0.24, note: '零售靠走量，毛利薄，库存周转比毛利更重要' },
  { key: '生鲜', name: '果蔬/生鲜/菜摊', margin_range: [0.20, 0.35], margin_default: 0.28, note: '生鲜损耗率 8%-15%，实际毛利要扣掉损耗再算' },
  { key: '服务', name: '美容/维修/洗护等', margin_range: [0.55, 0.80], margin_default: 0.68, note: '服务靠手艺和复购，人工是最大成本，老板亲自干回本最快' },
  { key: '摆摊', name: '流动摊位/夜市', margin_range: [0.50, 0.70], margin_default: 0.60, note: '摆摊轻资产，主要成本是摊位费+交通，试错成本低' }
]

Page({
  data: {
    presets: FALLBACK_PRESETS,
    bizType: '餐饮',
    bizTypeIndex: 0,
    bizTypeName: '',
    presetNote: '',
    marginHint: '',
    // 表单
    dailyRevenue: '',
    grossMargin: '',
    rent: '',
    salary: '',
    utilities: '',
    totalInvestment: '',
    cashOnHand: '',
    traffic: '一般',
    competitor: '一般',
    trafficOptions: ['差', '一般', '好'],
    competitorOptions: ['多', '一般', '少'],
    // 结果（paybackText/cashText/monthGross 为 WXML 友好的预格式化字段）
    result: null,
    paybackText: '',
    cashText: '',
    monthGrossText: '',
    calcLoading: false
  },

  onLoad() {
    this.loadPresets()
  },

  loadPresets() {
    api.storePresets()
      .then(r => {
        const presets = r.presets || FALLBACK_PRESETS
        this.setData({ presets })
        this.applyPreset('餐饮')
      })
      .catch(() => {
        this.applyPreset('餐饮')
      })
  },

  applyPreset(key) {
    const idx = this.data.presets.findIndex(x => x.key === key)
    const p = idx >= 0 ? this.data.presets[idx] : this.data.presets[0]
    if (!p) return
    this.setData({
      bizType: p.key,
      bizTypeIndex: idx >= 0 ? idx : 0,
      bizTypeName: p.name,
      presetNote: p.note,
      marginHint: `参考 ${Math.round(p.margin_range[0] * 100)}%-${Math.round(p.margin_range[1] * 100)}%（不填用默认 ${Math.round(p.margin_default * 100)}%）`,
      grossMargin: ''
    })
  },

  onBizChange(e) {
    const idx = Number(e.detail.value)
    const p = this.data.presets[idx]
    if (p) this.applyPreset(p.key)
  },

  onInput(e) {
    this.setData({ [e.currentTarget.dataset.key]: e.detail.value })
  },

  onTrafficChange(e) { this.setData({ traffic: e.detail.value }) },
  onCompetitorChange(e) { this.setData({ competitor: e.detail.value }) },

  calcModel() {
    const d = this.data
    const hasAny = [d.dailyRevenue, d.rent, d.salary, d.utilities, d.totalInvestment, d.cashOnHand]
      .some(v => String(v).trim() !== '')
    if (!hasAny) {
      wx.showToast({ title: '请至少填一项数据', icon: 'none' })
      return
    }
    this.setData({ calcLoading: true })
    api.storeModel({
      daily_revenue: parseFloat(d.dailyRevenue) || 0,
      gross_margin: d.grossMargin ? (parseFloat(d.grossMargin) / 100) : null,
      rent: parseFloat(d.rent) || 0,
      salary: parseFloat(d.salary) || 0,
      utilities: parseFloat(d.utilities) || 0,
      total_investment: parseFloat(d.totalInvestment) || 0,
      cash_on_hand: parseFloat(d.cashOnHand) || 0,
      traffic: d.traffic,
      competitor: d.competitor,
      biz_type: d.bizType
    })
      .then(r => {
        // 预格式化：null（回本不了/无固定支出）显示为 ∞
        const m = r.model || {}
        this.setData({
          result: r,
          paybackText: m.payback_months == null ? '∞' : m.payback_months,
          cashText: m.cash_months == null ? '∞' : m.cash_months,
          monthGrossText: Math.round((m.month_revenue || 0) * ((r.inputs && r.inputs.gross_margin) || 0)),
          calcLoading: false
        })
      })
      .catch(() => {
        this.setData({ calcLoading: false })
        wx.showToast({ title: '算账失败，请确认后端已启动', icon: 'none' })
      })
  }
})