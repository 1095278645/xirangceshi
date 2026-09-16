// pages/index/index.js 语音记账
const api = require('../../utils/api')
const app = getApp()

// 同声传译插件为可选能力：正式 AppID 授权后可用，否则降级为手动输入
let manager = null
try {
  const plugin = requirePlugin('WechatSI')
  manager = plugin.getRecordRecognitionManager()
} catch (e) {
  console.warn('[AI掌柜] 同声传译插件不可用（需正式 AppID 并授权），语音已降级为手动输入')
}

Page({
  data: {
    shopName: '我的小店',
    summary: { income: 0, expense: 0, balance: 0, cnt: 0 },
    month: { period: '', income: 0, expense: 0, balance: 0 },
    recognizing: false,
    voiceEnabled: !!manager,
    result: '',
    submitting: false,
    parsed: null,       // 解析结果
    voucher: null,      // 凭证信息
    friendlyCategory: '',
    // 金额没听懂时的草稿（不落库，等店主补金额）
    amountDraft: null,
    amountInput: '',
    // 记成之后 AI 的最终理解（金额/方向/分类/熟客），显示出来供当场核对与更正
    recorded: null,
    manualText: '',
    review: '',          // 掌柜今日复盘
    connBroken: false,   // 后端连不上时显示常驻提示条
    serverAddr: ''
  },

  onLoad() {
    this.shopName = app.globalData.shopName
    this.setData({ shopName: this.shopName, serverAddr: api.getBaseUrl() })
    api.resetFailFlag()          // 进入页面重置连接错误提示标志，避免一直不提示
    this.initRecognizer()
    this.loadSummary()
    this.loadMonth()
    this.loadReview()
  },

  onShow() {
    this.loadSummary()
    this.loadMonth()
  },

  // 加载类接口失败时给出提示（原先静默失败，演示时看不出是地址配错了）
  loadSummary() {
    api.todaySummary()
      .then(s => this.setData({ summary: s, connBroken: false }))
      .catch(err => this.markConnError(err))
  },

  loadMonth() {
    api.monthlySummary()
      .then(m => this.setData({ month: m, connBroken: false }))
      .catch(err => this.markConnError(err))
  },

  loadReview() {
    api.heartbeat().then(h => {
      this.setData({ review: (h && h.ok && h.review) ? h.review : '' })
    }).catch(err => this.markConnError(err))
  },

  // 失败时既弹一次提示，也把首页顶部提示条点亮（持续可见，便于当场排查）
  markConnError(err) {
    api.reportError(err)
    this.setData({ connBroken: !!api.connectionBroken() })
  },

  // 点提示条 → 直接去设置页配地址
  goSettings() {
    wx.reLaunch({ url: '/pages/settings/settings' })
  },

  // 初始化语音识别（微信同声传译插件，可选）
  initRecognizer() {
    if (!manager) return
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
    if (!manager) {
      wx.showToast({ title: '语音需正式AppID授权，请用下方手动输入', icon: 'none' })
      return
    }
    this.setData({ recognizing: true, result: '', parsed: null })
    manager.start({ lang: 'zh_CN', duration: 10000 })
  },

  endRecord() {
    if (!manager) return
    manager.stop()
  },

  cancelRecord() {
    if (!manager) return
    manager.stop()
    this.setData({ recognizing: false })
  },

  // 提交记账
  submitOrder(text) {
    this.setData({ submitting: true })
    api.createOrder(text)
      .then(res => {
        // WXML 不支持方法调用：tags 在 JS 层拆成数组
        const parsed = res.parsed
        if (parsed && parsed.tags) {
          parsed.tagsArr = String(parsed.tags).split(',').filter(Boolean)
        }
        // 金额没听懂：**没有落库**，进入"补金额"状态（rather than 留一条 0 元幽灵记录）
        if (res.amount_missing) {
          this.setData({
            parsed,
            voucher: null,
            friendlyCategory: '',
            amountDraft: res.draft || {},
            amountInput: '',
            recorded: null,
            submitting: false,
            manualText: ''
          })
          return
        }
        this.setData({
          parsed,
          voucher: res.voucher,
          friendlyCategory: res.friendly_category,
          // AI 到底把这句话记成了什么：四项全摆出来，店主当场就能核对
          recorded: res.recorded || null,
          amountDraft: null,
          submitting: false,
          summary: res.summary,
          manualText: ''
        })
        this.loadMonth()
        if (res.customer_new) {
          wx.showToast({ title: '新熟客已记住', icon: 'none' })
        }
      })
      .catch(err => {
        this.setData({ submitting: false })
        wx.showToast({ title: err.message, icon: 'none' })
      })
  },

  // ---------- 补记金额（AI 没听懂金额时的追问） ----------
  onAmountInput(e) {
    this.setData({ amountInput: e.detail.value })
  },

  confirmAmount() {
    const d = this.data.amountDraft
    if (!d) return
    const amt = parseFloat(this.data.amountInput)
    if (!amt || amt <= 0) {
      wx.showToast({ title: '填一个大于 0 的金额', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    // 把 AI 已解析好的字段原样带回去：后端不再重解析，科目/熟客与草稿完全一致
    api.createOrder(d.text || d.item, {
      amount: amt, customer: d.customer, item: d.item,
      category: d.category, trans_type: d.trans_type, note: d.note
    }).then(res => {
      this.setData({
        recorded: res.recorded || null,
        voucher: res.voucher,
        friendlyCategory: res.friendly_category,
        amountDraft: null,
        amountInput: '',
        submitting: false,
        summary: res.summary
      })
      this.loadMonth()
      wx.showToast({ title: '已补记 ' + amt + ' 元', icon: 'success' })
    }).catch(err => {
      this.setData({ submitting: false })
      wx.showToast({ title: err.message, icon: 'none' })
    })
  },

  cancelAmount() {
    this.setData({ amountDraft: null, amountInput: '', parsed: null })
  },

  // ---------- 就地更正（AI 记错了，别等到月底才发现） ----------
  fixRecorded() {
    const r = this.data.recorded
    if (!r) return
    wx.showActionSheet({
      itemList: ['改金额', '改成支出', '改成收入'],
      success: res => {
        if (res.tapIndex === 0) {
          wx.showModal({
            title: '改成多少？', editable: true, content: String(r.amount),
            success: m => {
              if (!m.confirm) return
              const v = parseFloat(m.content)
              if (isNaN(v) || v < 0) {
                wx.showToast({ title: '金额不对', icon: 'none' })
                return
              }
              this.applyFix({ amount: v, reason: '店主当场更正金额' })
            }
          })
        } else if (res.tapIndex === 1 || res.tapIndex === 2) {
          const want = res.tapIndex === 1 ? 'expense' : 'income'
          if (want === r.trans_type) {
            wx.showToast({ title: '本来就是' + (want === 'income' ? '收入' : '支出'),
                           icon: 'none' })
            return
          }
          this.applyFix({ trans_type: want, reason: '店主当场更正收支方向' })
        }
      }
    })
  },

  applyFix(patch) {
    const r = this.data.recorded
    if (!r || !r.transaction_id) return
    const body = {
      reason: patch.reason || '就地更正',
    }
    if (patch.amount !== undefined) body.amount = patch.amount
    if (patch.trans_type !== undefined) body.trans_type = patch.trans_type
    api.editTransaction(r.transaction_id, body)
      .then(res => {
        const t = (res && res.transaction) || {}
        this.setData({
          recorded: Object.assign({}, r, {
            amount: t.amount !== undefined ? t.amount : r.amount,
            trans_type: t.trans_type || r.trans_type
          })
        })
        this.loadMonth()
        wx.showToast({ title: '已更正（留痕可查）', icon: 'none' })
      })
      .catch(err => wx.showToast({ title: err.message, icon: 'none' }))
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