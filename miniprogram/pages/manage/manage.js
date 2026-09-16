// pages/manage/manage.js 管理台：会计三表 / 数据备份 / 主动触达 / 多店入口
//
// 为什么单独一页而不是塞进「账本」：账本页已经有 7 个标签（流水/算税/科目/报表/
// 现金/库存/发票），再加会把演示动线拉得太长。这些是**低频但重要**的管理动作，
// 集中在一页，从「设置」或「账本」进得来即可。
const api = require('../../utils/api.js')

Page({
  data: {
    shop: null,          // 当前店铺信息
    shopCount: 0,        // 多店时才显示切换区
    tab: 'account',      // account | backup | notify
    period: '',          // 会计期间 YYYY-MM
    loading: false,
    // 会计
    income: null, balance: null, trial: null, closings: [],
    // 备份
    backups: [], backupBusy: false,
    // 触达
    providers: [], subs: [], logs: [], inbox: []
  },

  onLoad() {
    const d = new Date()
    this.setData({
      period: d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
    })
    this.loadContext()
    this.loadAccount()
    this.loadBackups()
    this.loadNotify()
  },

  onShow() { this.loadContext() },

  // ---------- 店铺上下文 ----------
  loadContext() {
    api.shopContext().then(r => {
      this.setData({ shop: r.shop, shopCount: (r.my_shops || []).length })
    }).catch(err => api.reportError(err))
  },

  switchTab(e) {
    const tab = e.currentTarget.dataset.tab
    this.setData({ tab })
    if (tab === 'account') this.loadAccount()
    if (tab === 'backup') this.loadBackups()
    if (tab === 'notify') this.loadNotify()
  },

  goShops() { wx.navigateTo({ url: '/pages/shops/shops' }) },
  goCollect() { wx.navigateTo({ url: '/pages/collect/collect' }) },

  onPeriod(e) {
    this.setData({ period: e.detail.value })
    this.loadAccount()
  },

  // ---------- 会计闭环 ----------
  loadAccount() {
    const p = this.data.period
    // 资产负债表按"期末最后一天"切片（as_of 是日期，不是期间）
    const asOf = p ? p + '-31' : ''
    this.setData({ loading: true })
    Promise.all([
      api.incomeStatement(p).catch(() => null),
      api.balanceSheet(asOf).catch(() => null),
      api.trialBalance(p, true).catch(() => null),
      api.closings().catch(() => null)
    ]).then(([income, balance, trial, closings]) => {
      this.setData({
        income, balance, trial,
        closings: (closings && closings.closings) || [],
        loading: false
      })
    }).catch(err => {
      this.setData({ loading: false })
      api.reportError(err)
    })
  },

  closePeriod() {
    wx.showModal({
      title: '期末结转',
      content: `把 ${this.data.period} 的收入与费用结转到本年利润？结转后该期间的损益类科目归零。`,
      success: res => {
        if (!res.confirm) return
        api.closePeriod(this.data.period).then(r => {
          const np = r && r.net_profit
          wx.showToast({
            title: np === undefined || np === null
              ? '已结转' : `已结转，净利 ${np} 元`,
            icon: 'none', duration: 2500
          })
          this.loadAccount()
        }).catch(api.reportError)
      }
    })
  },

  reopenPeriod() {
    wx.showModal({
      title: '反结转',
      content: `撤销 ${this.data.period} 的期末结转？（会删掉结转凭证，可重新结转）`,
      success: res => {
        if (!res.confirm) return
        api.reopenPeriod(this.data.period).then(() => {
          wx.showToast({ title: '已反结转', icon: 'success' })
          this.loadAccount()
        }).catch(api.reportError)
      }
    })
  },

  // ---------- 备份 / 恢复 ----------
  loadBackups() {
    api.backups().then(r => {
      this.setData({ backups: (r && r.backups) || [] })
    }).catch(err => api.reportError(err))
  },

  createBackup() {
    this.setData({ backupBusy: true })
    api.createBackup('manual').then(r => {
      this.setData({ backupBusy: false })
      wx.showToast({ title: '备份完成', icon: 'success' })
      this.loadBackups()
    }).catch(err => {
      this.setData({ backupBusy: false })
      api.reportError(err)
    })
  },

  // 导出全量数据包
  //
  // 注意：后端有两个语义不同的端点，别搞混（我第一版就搞混过）：
  //   POST /api/backup/export          → 在服务器上**生成** zip 包，返回文件名
  //   GET  /api/backup/download/{name} → 真正把包下载下来
  // 所以先 POST 生成、拿 name，再下载。
  exportBundle() {
    const base = api.getBaseUrl()
    if (!base) return
    wx.showLoading({ title: '打包中…' })
    api.raw('/api/backup/export', 'POST', {}).then(r => {
      const name = r && r.export && r.export.name
      if (!name) throw new Error('导出失败：后端未返回文件名')
      wx.hideLoading()
      const url = api.backupDownloadUrl(name)
      // 小程序无法保证打开任意扩展名的文件，所以把链接给出来：
      // 开发者工具/电脑浏览器点开即可下载，也可以复制到电脑上用。
      wx.showModal({
        title: '数据包已生成',
        content: '下载地址（在浏览器打开即可保存）：\n' + url,
        confirmText: '复制链接',
        cancelText: '关闭',
        success: res => {
          if (res.confirm) {
            wx.setClipboardData({
              data: url,
              success: () => wx.showToast({ title: '链接已复制', icon: 'none' })
            })
          }
        }
      })
    }).catch(err => {
      wx.hideLoading()
      api.reportError(err)
    })
  },

  // 从聊天记录里选一个备份包（.zip / .db）上传恢复
  importBundle() {
    const base = api.getBaseUrl()
    if (!base) return
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['zip', 'db'],
      success: pick => {
        const f = pick.tempFiles && pick.tempFiles[0]
        if (!f) return
        wx.showModal({
          title: '导入并恢复',
          content: `将用「${f.name}」覆盖当前全部数据（恢复前会自动备份当前数据）。确认继续？`,
          confirmText: '确认恢复',
          confirmColor: '#b4532a',
          success: res => {
            if (!res.confirm) return
            wx.showLoading({ title: '恢复中…' })
            // 后端要求显式 confirm=true，否则拒收（防误操作）
            wx.uploadFile({
              url: base + '/api/backup/import?confirm=true&filename=' +
                encodeURIComponent(f.name || 'upload.zip'),
              filePath: f.path,
              name: 'file',            // 后端读的是原始请求体，字段名不影响
              header: api.authHeader(),
              success: up => {
                wx.hideLoading()
                if (up.statusCode >= 200 && up.statusCode < 300) {
                  wx.showToast({ title: '恢复完成', icon: 'success' })
                  this.loadBackups()
                  this.loadAccount()
                } else {
                  wx.showToast({ title: '恢复失败：' + up.statusCode, icon: 'none' })
                }
              },
              fail: () => {
                wx.hideLoading()
                wx.showToast({ title: '上传失败，请检查后端地址', icon: 'none' })
              }
            })
          }
        })
      }
    })
  },

  restoreBackup(e) {
    const name = e.currentTarget.dataset.name
    wx.showModal({
      title: '恢复数据',
      content: `用「${name}」覆盖当前数据？恢复前会自动再做一次备份。`,
      confirmText: '确认恢复',
      confirmColor: '#b4532a',
      success: res => {
        if (!res.confirm) return
        // 后端要求 confirm=true，否则直接 400（防误操作）
        api.raw('/api/backup/restore/' + encodeURIComponent(name) +
                '?confirm=true', 'POST', {})
          .then(() => {
            wx.showToast({ title: '已恢复', icon: 'success' })
            this.loadBackups()
            this.loadAccount()
          }).catch(api.reportError)
      }
    })
  },

  deleteBackup(e) {
    const name = e.currentTarget.dataset.name
    wx.showModal({
      title: '删除备份',
      content: `删除备份「${name}」？此操作不可撤销。`,
      success: res => {
        if (!res.confirm) return
        api.deleteBackup(name).then(() => {
          wx.showToast({ title: '已删除', icon: 'success' })
          this.loadBackups()
        }).catch(api.reportError)
      }
    })
  },

  // ---------- 主动触达 ----------
  loadNotify() {
    Promise.all([
      api.notifyProviders().catch(() => null),
      api.notifySubscriptions().catch(() => null),
      api.notifyLogs().catch(() => null),
      api.notifyMockInbox().catch(() => null)
    ]).then(([providers, subs, logs, inbox]) => {
      this.setData({
        providers: (providers && providers.providers) || [],
        subs: (subs && subs.subscriptions) || [],
        logs: (logs && logs.logs) || [],
        inbox: (inbox && inbox.messages) || []
      })
    })
  },

  testPush() {
    // 演示走本地记录通道（无需任何配置），实测最稳；通了再换真实通道
    const ch = (this.data.providers[0] && this.data.providers[0].id) || 'mock'
    api.notifyTest(ch, { event: 'daily_review', title: '测试推送',
                         content: '这是一条来自掌柜的测试消息' })
      .then(() => {
        wx.showToast({ title: '已发送测试推送', icon: 'success' })
        this.loadNotify()
      }).catch(api.reportError)
  },

  // 一步配置企业微信机器人：演示时最快能"看到推送真的发出去了"
  setupWecomBot() {
    wx.showModal({
      title: '企业微信机器人',
      editable: true,
      placeholderText: '粘贴 Webhook 地址或 key',
      success: res => {
        if (!res.confirm || !res.content) return
        api.notifyWecomBot(res.content.trim()).then(r => {
          const msg = (r && r.message) || '已配置'
          wx.showModal({ title: '配置结果', content: msg, showCancel: false })
          this.loadNotify()
        }).catch(api.reportError)
      }
    })
  }
})
