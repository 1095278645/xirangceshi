"""tax_data.py — 安全护栏边界场景静态数据，从 tax.py 拆出。

10 种边界场景的 keywords + advice，仅在 detect_boundary() 调用时引用。
"""
BOUNDARY_SCENARIOS = [
    {"code": "B01", "scene": "工商注册/变更", "level": "中高",
     "keywords": ("注册", "营业执照", "变更登记"), "advice": "需走工商流程，涉及章程、验资材料"},
    {"code": "B02", "scene": "税务稽查", "level": "极高",
     "keywords": ("稽查", "税务检查", "补税"), "advice": "按稽查通知书配合，保留账册凭证原件"},
    {"code": "B03", "scene": "股权转让", "level": "极高",
     "keywords": ("股权", "转让", "股东"), "advice": "需股权转让协议、评估报告与完税证明"},
    {"code": "B04", "scene": "投资理财", "level": "高",
     "keywords": ("投资", "理财", "基金", "股票"), "advice": "先做风险测评，确认收益属性与税务处理"},
    {"code": "B05", "scene": "跨境业务", "level": "高",
     "keywords": ("外币", "汇率", "进出口", "海关"), "advice": "涉及结售汇与海关单据，需专项处理"},
    {"code": "B06", "scene": "融资贷款", "level": "中高",
     "keywords": ("贷款", "融资", "抵押"), "advice": "签订贷款/抵押合同，留意利息税前扣除凭证"},
    {"code": "B07", "scene": "劳动纠纷", "level": "中",
     "keywords": ("劳动仲裁", "工伤", "赔偿"), "advice": "保留劳动合同、考勤与工资发放记录"},
    {"code": "B08", "scene": "知识产权", "level": "中",
     "keywords": ("专利", "商标", "侵权"), "advice": "保留注册/授权文件，侵权需律师介入"},
    {"code": "B09", "scene": "诉讼仲裁", "level": "极高",
     "keywords": ("起诉", "仲裁", "法院"), "advice": "诉讼时效与证据保全，建议尽快请律师"},
    {"code": "B10", "scene": "年度汇算", "level": "中高",
     "keywords": ("汇算清缴", "年度申报"), "advice": "汇算前核对账目、发票与费用凭证"},
]
