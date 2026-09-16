// pages/shops/shops.js 多店 / 多用户管理
//
// 演示动线：新建「二号店」→ 切过去记一笔 → 流水里看不到一号店的数据
// → 加一个店员账号（只读/记账权限）→ 用店员令牌访问会被限制在他所在的店。
const api = require('../../utils/api.js')

Page({
  data: {
    shops: [], users: [], currentId: null,
    members: [],        // 当前查看店铺的成员
    memberShopId: null,
    canManage: true,    // 无权限时隐藏写操作，避免点了才报错
    identity: null
  },

  onLoad() { this.loadAll() },
  onShow() { this.loadAll() },

  loadAll() {
    Promise.all([
      api.listShops().catch(() => null),
      api.listUsers().catch(() => null),
      api.shopContext().catch(() => null)
    ]).then(([shops, users, ctx]) => {
      const list = (shops && shops.shops) || []
      const me = (ctx && ctx.identity) || {}
      const canManage = me.kind === 'owner' || me.role === 'owner' || me.role === 'admin'
      this.setData({
        shops: list,
        users: (users && users.users) || [],
        currentId: (ctx && ctx.shop_id) || null,
        identity: me,
        canManage
      })
      const target = this.data.memberShopId || this.data.currentId ||
        (list[0] && list[0].id)
      if (target) this.loadMembers(target)
    })
  },

  loadMembers(shopId) {
    api.shopMembers(shopId).then(r => {
      this.setData({ members: (r && r.members) || [], memberShopId: shopId })
    }).catch(() => this.setData({ members: [], memberShopId: shopId }))
  },

  onSelectShop(e) {
    this.loadMembers(Number(e.currentTarget.dataset.id))
  },

  // ---------- 切店 ----------
  switchShop(e) {
    const id = Number(e.currentTarget.dataset.id)
    api.switchShop(id).then(r => {
      api.setShopId(id)                      // 之后所有请求都带 X-Shop-Id
      this.setData({ currentId: id })
      wx.showToast({ title: '已切到「' + ((r.shop && r.shop.name) || id) + '」',
                     icon: 'none', duration: 2000 })
      this.loadAll()
    }).catch(api.reportError)
  },

  // ---------- 新建店铺 ----------
  createShop() {
    wx.showModal({
      title: '新建店铺',
      editable: true,
      placeholderText: '店名，如：巷口二号店',
      success: res => {
        if (!res.confirm || !res.content) return
        // copy_from 让新店沿用当前店的科目结构（不复制流水）
        api.createShop({ name: res.content.trim(), copy_from: this.data.currentId })
          .then(r => {
            wx.showToast({ title: '已创建', icon: 'success' })
            this.loadAll()
          }).catch(api.reportError)
      }
    })
  },

  renameShop(e) {
    const { id, name } = e.currentTarget.dataset
    wx.showModal({
      title: '修改店名', editable: true, content: name,
      success: res => {
        if (!res.confirm || !res.content) return
        api.updateShop(Number(id), { name: res.content.trim() })
          .then(() => this.loadAll()).catch(api.reportError)
      }
    })
  },

  archiveShop(e) {
    const id = Number(e.currentTarget.dataset.id)
    wx.showModal({
      title: '删除店铺',
      content: '仅从列表移除，数据文件会保留（可联系技术支持找回）。确认删除？',
      confirmColor: '#b4532a',
      success: res => {
        if (!res.confirm) return
        api.deleteShop(id).then(() => {
          wx.showToast({ title: '已移除', icon: 'success' })
          this.loadAll()
        }).catch(api.reportError)
      }
    })
  },

  // ---------- 成员 ----------
  addUser() {
    wx.showModal({
      title: '新增成员',
      editable: true,
      placeholderText: '姓名，如：小李',
      success: res => {
        if (!res.confirm || !res.content) return
        const name = res.content.trim()
        wx.showActionSheet({
          itemList: ['店员（可记账）', '店长（可管账+管人）', '店主（全权）'],
          success: pick => {
            const role = ['staff', 'admin', 'owner'][pick.tapIndex]
            const shopIds = this.data.memberShopId ? [this.data.memberShopId] : []
            api.createUser({ name, role, shop_ids: shopIds }).then(r => {
              const token = r.user && r.user.token
              wx.showModal({
                title: '创建成功',
                content: '令牌（只显示这一次）：\n' + token +
                  '\n\n请复制后发给该成员，让他填到「设置」页的访问令牌里。',
                showCancel: false,
                confirmText: '我已复制'
              })
              this.loadAll()
            }).catch(api.reportError)
          }
        })
      }
    })
  },

  changeRole(e) {
    const { id, name } = e.currentTarget.dataset
    wx.showActionSheet({
      itemList: ['店员（可记账）', '店长（可管账+管人）'],
      success: pick => {
        const role = ['staff', 'admin'][pick.tapIndex]
        api.updateUser(Number(id), { role }).then(() => {
          wx.showToast({ title: '已改为' + (role === 'staff' ? '店员' : '店长'),
                         icon: 'none' })
          this.loadAll()
        }).catch(api.reportError)
      }
    })
  },

  removeUser(e) {
    const { id, name } = e.currentTarget.dataset
    wx.showModal({
      title: '移除成员',
      content: `把「${name}」从系统中移除？他的令牌会立即失效。`,
      confirmColor: '#b4532a',
      success: res => {
        if (!res.confirm) return
        api.deleteUser(Number(id)).then(() => {
          wx.showToast({ title: '已移除', icon: 'success' })
          this.loadAll()
        }).catch(api.reportError)
      }
    })
  },

  grantMember(e) {
    const uid = Number(e.currentTarget.dataset.id)
    const sid = this.data.memberShopId
    if (!sid) return
    api.grantMember(sid, uid, 'staff').then(() => {
      wx.showToast({ title: '已加入本店', icon: 'none' })
      this.loadMembers(sid)
    }).catch(api.reportError)
  },

  revokeMember(e) {
    const uid = Number(e.currentTarget.dataset.id)
    const sid = this.data.memberShopId
    if (!sid) return
    api.revokeMember(sid, uid).then(() => {
      wx.showToast({ title: '已移出本店', icon: 'none' })
      this.loadMembers(sid)
    }).catch(api.reportError)
  }
})
