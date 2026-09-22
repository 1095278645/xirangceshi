"""benchmark.py — 同业基准（脱敏示例）

## 诚实声明

真实同业数据需要平台侧汇聚，本模块**不含任何真实门店数据**：下面的区间是
按公开行业经验整理的**示例/仿真值**，仅用于演示"同类对比"这一产品形态与
交互，接口返回会带 `disclaimer` 与 `source: "示例/仿真，非真实行业数据"`。
上线真实版本时，把 REFERENCE 换成脱敏聚合结果即可，接口契约不变。

## 产品意图

单看"我的毛利率 62%"没有意义；店主需要的是"同业态一般多少、我处在什么位置"。
有了参照系，经营建议才落地。
"""
from __future__ import annotations

# 示例参考区间：[下限, 上限]；日销单位元，毛利率为小数，客单价单位元
REFERENCE: dict[str, dict] = {
    "餐饮": {"gross_margin": (0.55, 0.70), "avg_ticket": (8, 30),   "daily_revenue": (800, 3000)},
    "饮品": {"gross_margin": (0.60, 0.80), "avg_ticket": (10, 25),  "daily_revenue": (600, 2500)},
    "零售": {"gross_margin": (0.20, 0.40), "avg_ticket": (15, 60),  "daily_revenue": (1000, 5000)},
    "生鲜": {"gross_margin": (0.15, 0.30), "avg_ticket": (20, 50),  "daily_revenue": (2000, 8000)},
    "服务": {"gross_margin": (0.50, 0.75), "avg_ticket": (50, 200), "daily_revenue": (500, 3000)},
    "摆摊": {"gross_margin": (0.50, 0.70), "avg_ticket": (5, 20),   "daily_revenue": (300, 1500)},
}
DEFAULT_BIZ = "餐饮"
DISCLAIMER = ("参考区间为示例/仿真值，非真实行业数据，仅用于展示同类对比形态；"
              "接入真实脱敏数据后结论才具参考意义。")


def _level(value, lo, hi) -> str:
    if value is None:
        return "无数据"
    if value < lo:
        return "低于区间"
    if value > hi:
        return "高于区间"
    return "区间内"


def _hint(metric: str, level: str) -> str:
    table = {
        ("gross_margin", "低于区间"): "毛利率偏低：优先复核进货价与损耗，必要时调结构（多推高毛利品）。",
        ("gross_margin", "高于区间"): "毛利率偏高：留意是否少记了进货成本，或定价是否有流失风险。",
        ("avg_ticket", "低于区间"): "客单价偏低：可试搭售/套餐，把单品成交抬成组合成交。",
        ("avg_ticket", "高于区间"): "客单价偏高：确认是否符合业态，提防把大额单当常态。",
        ("daily_revenue", "低于区间"): "日销偏低：先看保本线——低于保本线就是“开门亏一天”。",
        ("daily_revenue", "高于区间"): "日销较高：关注翻台/产能上限，可考虑提价或加人手。",
    }
    return table.get((metric, level), "")


def compare(biz_type: str, stats: dict, avg_ticket: float | None) -> dict:
    """把本店实际指标与示例参考区间对比。stats 来自 db.store_ledger_stats。"""
    ref = REFERENCE.get(biz_type) or REFERENCE[DEFAULT_BIZ]
    own = {
        "gross_margin": stats.get("gross_margin"),
        "avg_ticket": avg_ticket,
        "daily_revenue": stats.get("daily_revenue"),
    }
    labels = {"gross_margin": "毛利率", "avg_ticket": "客单价", "daily_revenue": "日销"}
    items = []
    for key in ("gross_margin", "avg_ticket", "daily_revenue"):
        lo, hi = ref[key]
        val = own[key]
        lvl = _level(val, lo, hi)
        items.append({
            "metric": key, "label": labels[key], "own": val,
            "ref_range": [lo, hi], "level": lvl, "hint": _hint(key, lvl),
        })
    return {
        "biz_type": biz_type if biz_type in REFERENCE else DEFAULT_BIZ,
        "reference": {k: list(v) for k, v in ref.items()},
        "items": items,
        "matched": biz_type in REFERENCE,
        "source": "示例/仿真，非真实行业数据",
        "disclaimer": DISCLAIMER,
    }
