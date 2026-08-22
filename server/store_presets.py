"""store_presets.py — 业态预设（参考毛利率区间），从 store.py 拆出。

静态数据，按需 import；store.py 的计算逻辑不依赖此文件的结构。
"""
BUSINESS_PRESETS = {
    "餐饮": {
        "name": "餐饮（快餐/面馆/早餐）",
        "margin_range": (0.50, 0.65),
        "margin_default": 0.58,
        "person_range": (1, 4),
        "note": "餐饮的核心是翻台率和出餐效率，房租占比别超营业额 15%",
    },
    "饮品": {
        "name": "茶饮/咖啡/甜品",
        "margin_range": (0.55, 0.70),
        "margin_default": 0.62,
        "person_range": (1, 3),
        "note": "饮品毛利高但极度依赖客流，选址=生死线",
    },
    "零售": {
        "name": "便利店/超市/杂货",
        "margin_range": (0.18, 0.30),
        "margin_default": 0.24,
        "person_range": (1, 3),
        "note": "零售靠走量，毛利薄，库存周转比毛利更重要",
    },
    "生鲜": {
        "name": "果蔬/生鲜/菜摊",
        "margin_range": (0.20, 0.35),
        "margin_default": 0.28,
        "person_range": (1, 2),
        "note": "生鲜损耗率 8%-15%，实际毛利要扣掉损耗再算",
    },
    "服务": {
        "name": "美容/维修/洗护等",
        "margin_range": (0.55, 0.80),
        "margin_default": 0.68,
        "person_range": (1, 3),
        "note": "服务靠手艺和复购，人工是最大成本，老板亲自干回本最快",
    },
    "摆摊": {
        "name": "流动摊位/夜市",
        "margin_range": (0.50, 0.70),
        "margin_default": 0.60,
        "person_range": (1, 1),
        "note": "摆摊轻资产，主要成本是摊位费+交通，试错成本低",
    },
}
PRESET_KEYS = tuple(BUSINESS_PRESETS.keys())
