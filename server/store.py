"""单店经营模型计算（源自勇哥·餐饮反诈操作系统，泛化到多业态）

核心公式（勇哥的"保本线先行"）：
    月固定成本 = 房租 + 人工 + 水电杂费
    保本月销 = 月固定成本 ÷ 毛利率
    保本日销 = 保本月销 ÷ 30
    目标日销 = 保本日销 × 1.3        # 30% 安全垫
    月利润   = 月营业额 × 毛利率 - 月固定成本
    回本周期 = 总投资 ÷ 月利润        # 月
    现金流可扛月数 = 现有现金 ÷ 月固定成本

三维交叉验证（勇哥 Step4）：
  A 经营现金流：实际日销 vs 保本线 vs 目标线
  B 投资回本：   回本周期是否在安全区间
  C 商圈客流：   客流档位（差/一般/好）+ 周边竞争

结论：健康 / 临界（整改窗口）/ 危险（止损预警），附"掌柜一句话"。
业态预设拆到 store_presets.py；规则内嵌为 Python 常量，不依赖外部 YAML。
"""
from store_presets import BUSINESS_PRESETS, PRESET_KEYS  # noqa: F401

# ========== 阈值常量 ==========

SAFETY_RATIO = 1.3      # 勇哥标准：目标日销 = 保本线 × 1.3
MONTH_DAYS = 30         # 简化取 30 天/月
PAYBACK_MONTHS_SAFE = 12   # 回本周期 ≤12 个月为健康
PAYBACK_MONTHS_LIMIT = 24  # >24 个月视为危险
CASH_MONTHS_DANGER = 3     # 现金流只能扛 ≤3 个月视为危险
CASH_MONTHS_WARN = 6       # ≤6 个月视为临界


def _f(v, default=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def get_preset(biz: str) -> dict:
    """按业态返回预设；未知业态回退到通用默认"""
    p = BUSINESS_PRESETS.get(biz or "")
    if not p:
        return {
            "name": biz or "其他",
            "margin_range": (0.30, 0.60),
            "margin_default": 0.45,
            "person_range": (1, 4),
            "note": "自定义业态：毛利率按你实际填写的为准",
            "fallback": True,
        }
    return {**p, "fallback": False}


def _round_or_none(v, ndigits=1):
    if v == float("inf") or v is None:
        return None
    return round(v, ndigits)

_round_str = _round_or_none  # 别名，供外部引用


def _gen_advice(a_level, b_level, c_level, ratio, revenue, break_even_day,
                target_day, payback_months, cash_months, month_profit,
                invest, margin, preset) -> str:
    """勇哥式处方，AI 掌柜的暖调：先给结论，再给可执行动作"""
    lines = []
    # 结论句
    if a_level == "危险":
        lines.append(f"实际日销 {revenue:,.0f} 元低于保本线 {break_even_day:,.0f} 元，"
                     f"每开一天门都在往里面搭钱——这不是生意，是烧钱。")
    elif a_level == "临界":
        lines.append(f"日销 {revenue:,.0f} 元刚过保本线 {break_even_day:,.0f} 元，"
                     f"离目标线 {target_day:,.0f} 元还差 {max(target_day - revenue, 0):,.0f} 元，"
                     f"现在是白忙阶段，先把安全垫补出来。")
    else:
        lines.append(f"日销 {revenue:,.0f} 元已过目标线 {target_day:,.0f} 元，"
                     f"现金流是稳的，剩下的就是让利润再厚一点。")
    # 回本
    if payback_months is None or payback_months == float("inf"):
        if invest > 0:
            lines.append(f"按现在的利润，{invest:,.0f} 元投资回不了本，投入越多越危险。")
    elif payback_months <= PAYBACK_MONTHS_SAFE:
        lines.append(f"回本周期约 {payback_months:.1f} 个月，一年内回本，投资回报可以。")
    else:
        lines.append(f"回本要 {payback_months:.1f} 个月，超过一年回本偏慢，想办法提高毛利或降成本。")
# 现金流
    if cash_months is not None and cash_months <= CASH_MONTHS_WARN:
        lines.append(f"现金只够扛 {cash_months:.1f} 个月，建议至少备足 6 个月固定支出再谈扩张。")
    # 商圈
    if c_level == "危险":
        lines.append("这个位置的客流撑不起现在的店，别指望靠运气翻盘，天花板就摆在那。")
    # 动作建议（勇哥式"先做什么"）
    if a_level != "健康":
        lines.append("接下来：① 先算清每日固定成本，挂墙上；② 做 1 个月整改窗口，日销追到目标线；"
                     "③ 差太远就果断止损，沉没成本不是成本。")
    else:
        lines.append("稳住现金流，把赚到的钱留出 6 个月安全垫，再想扩店或加品。")
    return " ".join(lines)


def calc_store_model(
    daily_revenue: float = 0,        # 实际日营业额（元）
    gross_margin: float | None = None,   # 毛利率（小数），None 用业态默认
    rent: float = 0,                 # 月房租
    salary: float = 0,               # 月人工（含老板自己的机会成本）
    utilities: float = 0,            # 月水电杂费
    total_investment: float = 0,     # 总投资（含转让费/装修/设备）
    cash_on_hand: float = 0,         # 现有现金
    traffic: str = "一般",           # 商圈客流：差 / 一般 / 好
    competitor: str = "一般",        # 周边竞争：多 / 一般 / 少
    biz_type: str = "餐饮",
) -> dict:
    """单店模型计算：保本线 + 目标线 + 现金流 + 三维诊断（泛化多业态）"""
    preset = get_preset(biz_type)
    revenue = _f(daily_revenue)
    rent = _f(rent)
    salary = _f(salary)
    utils = _f(utilities)
    invest = _f(total_investment)
    cash = _f(cash_on_hand)

    # 毛利率：优先用户实际值，否则业态默认
    if gross_margin is None or gross_margin <= 0:
        margin = preset["margin_default"]
    else:
        margin = min(_f(gross_margin), 0.95)
    margin = max(margin, 0.05)

    fixed_month = rent + salary + utils
    break_even_month = fixed_month / margin if margin > 0 else float("inf")
    break_even_day = break_even_month / MONTH_DAYS
    target_day = break_even_day * SAFETY_RATIO

    month_revenue = revenue * MONTH_DAYS
    month_profit = month_revenue * margin - fixed_month
    if month_profit > 0:
        payback_months = invest / month_profit
    else:
        payback_months = float("inf")
    cash_months = cash / fixed_month if fixed_month else float("inf")

    # ---------- 三维诊断 ----------
    # A. 经营现金流（保本线对照）
    if revenue <= 0 or break_even_day == float("inf"):
        a_score = 0
    elif revenue >= target_day:
        a_score = 100
    elif revenue >= break_even_day:
        a_score = 50
    else:
        a_score = 0
    a_level = "健康" if a_score == 100 else ("临界" if a_score == 50 else "危险")

    # B. 投资回本（回本周期）
    if payback_months == float("inf"):
        b_score = 0
    elif payback_months <= PAYBACK_MONTHS_SAFE:
        b_score = 100
    elif payback_months <= PAYBACK_MONTHS_LIMIT:
        b_score = 50
    else:
        b_score = 0
    b_level = "健康" if b_score == 100 else ("临界" if b_score == 50 else "危险")

    # C. 商圈客流（客流档位 + 竞争）
    traffic_map = {"差": 1, "一般": 2, "好": 3}
    compet_map = {"多": 1, "一般": 2, "少": 3}
    t = traffic_map.get(traffic, 2)
    c = compet_map.get(competitor, 2)
    c_score = t * 30 + c * 10
    c_level = "健康" if c_score >= 70 else ("临界" if c_score >= 50 else "危险")

    # 综合：A 权重最高（现金流游戏）
    overall = round(a_score * 0.5 + b_score * 0.3 + c_score * 0.2)
    if overall >= 70:
        verdict, verdict_key = "健康", "ok"
    elif overall >= 40:
        verdict, verdict_key = "临界", "warn"
    else:
        verdict, verdict_key = "危险", "danger"

    # 现金流独立预警
    cash_flags = []
    if fixed_month > 0 and cash > 0:
        if cash_months <= CASH_MONTHS_DANGER:
            cash_flags.append(f"现有现金约可撑 {cash_months:.1f} 个月，≤3 个月属危险区，先保命")
        elif cash_months <= CASH_MONTHS_WARN:
            cash_flags.append(f"现有现金约可撑 {cash_months:.1f} 个月，建议备足 6 个月安全垫")

    ratio = revenue / target_day * 100 if target_day else None
    advice = _gen_advice(
        a_level, b_level, c_level, ratio, revenue, break_even_day, target_day,
        payback_months, cash_months, month_profit, invest, margin, preset)

    return {
        "preset": {
            "key": biz_type,
            "name": preset["name"],
            "note": preset["note"],
            "margin_range": list(preset["margin_range"]),
            "margin_default": preset["margin_default"],
            "fallback": preset.get("fallback", False),
        },
        "inputs": {
            "daily_revenue": revenue,
            "gross_margin": round(margin, 3),
            "rent": rent,
            "salary": salary,
            "utilities": utils,
            "total_investment": invest,
            "cash_on_hand": cash,
            "traffic": traffic,
            "competitor": competitor,
        },
        "model": {
            "fixed_month": round(fixed_month, 1),
            "break_even_month": round(break_even_month, 1),
            "break_even_day": round(break_even_day, 1),
            "target_day": round(target_day, 1),
            "month_revenue": round(month_revenue, 1),
            "month_profit": round(month_profit, 1),
            "payback_months": _round_str(payback_months),
            "cash_months": _round_str(cash_months),
        },
        "dimensions": {
            "a": {"name": "经营现金流", "score": a_score, "level": a_level, "ratio": round(ratio, 1) if ratio else None},
            "b": {"name": "投资回本", "score": b_score, "level": b_level,
                  "payback_months": _round_str(payback_months)},
            "c": {"name": "商圈客流", "score": c_score, "level": c_level,
                  "traffic": traffic, "competitor": competitor},
        },
        "overall": {"score": overall, "level": verdict, "key": verdict_key},
        "cash_flags": cash_flags,
        "advice": advice,
        "biz_rule": "保本线是店的命线：日销低于保本线，开门一天亏一天；低于目标线，白忙不赚钱",
    }