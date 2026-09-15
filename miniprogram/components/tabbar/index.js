// components/tabbar/index.js 底部导航
Component({
  properties: {
    current: { type: Number, value: 0 }   // 0=记账 1=熟客 2=文案 3=账本 4=单店 5=设置
  },
  methods: {
    go(e) {
      const idx = Number(e.currentTarget.dataset.index)
      const pages = ['/pages/index/index', '/pages/memory/memory', '/pages/copy/copy', '/pages/books/books', '/pages/store/store', '/pages/settings/settings']
      // idx 转 Number 再比较，避免字符串 '0' !== 0 恒为真导致点击当前 tab 也 reLaunch
      if (idx !== this.data.current) {
        wx.reLaunch({ url: pages[idx] })
      }
    }
  }
})