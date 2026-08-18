// components/tabbar/index.js 底部导航
Component({
  properties: {
    current: { type: Number, value: 0 }   // 0=记账 1=熟客 2=文案
  },
  methods: {
    go(e) {
      const idx = e.currentTarget.dataset.index
      const pages = ['/pages/index/index', '/pages/memory/memory', '/pages/copy/copy']
      if (idx !== this.data.current) {
        wx.reLaunch({ url: pages[idx] })
      }
    }
  }
})