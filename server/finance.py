"""finance.py — 现金流滚动预测引擎（纯函数，可单测）

对标 Firefly III 的现金流预测思路：把「静态可扛月数」升级为「未来 N 个月滚动预测」。
掌柜口径（亲民）：店主只关心「这个月还够不够花 / 哪个月光最紧 / 要不要提前备钱」。

原则：
  - 期初现金 + 未来每月流入(月均收入+应收到期) - 未来每月流出(月均支出+应付到期) = 期末现金
  - 纯本地算法，不依赖 AI；业务路由层负责从账本取数后调用本引擎。
  - 专业字段与亲民提示同时返回，前端只展示大白话部分。
"""
from __future__ import annotations

from datetime import date, datetime

DEFAULT_LOOKBACK = 3       # 用近 3 个月流水算月均基准
DEFAULT_FORECAST_MONTHS = 6  # 默认预测未来 6 个月
SAFETY_MONTHS = 1          # 期末现金低于「一个月支出」视为偏紧（亲民：钱快不够花）
DANGER_THRESHOLD = 0       # 期末现金 < 0 视为危险（亲民：这个月要垫钱）


def _f(v, default=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def month_key(dt) -> str:
    """把日期归到 'YYYY-MM' 月份键"""
    return dt.strftime("%Y-%m")


def shift_month(ym: str, delta: int) -> str:
    """月份键加减 delta 个月，返回 'YYYY-MM'"""
    y, m = int(ym[:4]), int(ym[5:7])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def forecast_cashflow(
    cash_on_hand: float = 0,
    base_income: float = 0,      # 月均基准收入（近 N 月均线）
    base_expense: float = 0,     # 月均基准支出
    debt_flows: list[dict] | None = None,  # 应收应付到期：[{month:'YYYY-MM', net:float}]
    months: int = DEFAULT_FORECAST_MONTHS,
    start_month: str | None = None,
    safety_buffer: float = 0,    # 月均固定成本（用于安全垫预警）
) -> dict:
    """未来 N 个月滚动现金流预测。

    入参：
      - cash_on_hand     期初现金
      - base_income      未来每月基础流入（历史月均收入线）
      - base_expense     未来每月基础流出（历史月均支出线）
      - debt_receives    应收应付到期计划：[{month, net}]，net 正=收回、负=支出
      - months           预测月数
      - start_month      'YYYY-MM'，默认当前月
      - safety_buffer    月均固定支出，用于判断「安全垫」
    返回：
      {
        "start_month", "start_cash",
        "months": [{month, inflow, outflow, net, end_balance, safe, danger}],
        "flags": [亲民大白话预警],
        "summary": "一句店主视角总结",
      }
    """
    income = _f(base_income)
    expense = _f(base_expense)
    safety = _f(safety_buffer)
    net_in = income - expense

    # 应收应付到期归集到月
    debt_plan: dict[str, float] = {}
    for d in (debt_flows or []):
        m = d.get("month") or month_key(date.today())
        # 注意 net 允许为负（应付到期=净流出），不能用 _f 过滤负数
        debt_plan[m] = debt_plan.get(m, 0.0) + float(d.get("net") or 0)

    today = date.today()
    cur = start_month or month_key(today)

    balance = _f(cash_on_hand)
    rows = []
    for i in range(max(1, int(months))):
        m = shift_month(cur, i)
        inflow = income
        outflow = expense
        # 应收应付到期：正=收回(计入流入)，负=支出(计入流出)
        net_debt = debt_plan.get(m, 0.0)
        if net_debt < 0:
            outflow += abs(net_debt)
        else:
            inflow += net_debt
        net = inflow - outflow
        balance += net
        # 亲民口径：安全与否
        safe = balance >= 0
        enough = safety <= 0 or balance >= safety
        rows.append({
            "month": m,
            "inflow": round(inflow, 2),
            "outflow": round(outflow, 2),
            "net": round(net, 2),
            "end_balance": round(balance, 2),
            "safe": safe,
            "enough": enough,
        })

    # ---------- 亲民预警 ----------
    flags = []
    for r in rows:
        if r["end_balance"] < 0:
            flags.append(f"{r['month']} 现金预计 -{abs(r['end_balance']):,.0f} 元，"
                         f"这个月得想办法垫钱，别硬撑。")
        elif safety > 0 and r["end_balance"] < safety:
            flags.append(f"{r['month']} 现金预计剩 {r['end_balance']:,.0f} 元，"
                         f"低于一个月的固定开销，要留神。")
    if not flags:
        last = rows[-1]["end_balance"]
        flags.append(f"照目前节奏，{rows[-1]['month']} 底现金预计还有 {last:,.0f} 元，"
                     f"现金流是稳的。")

    first = rows[0]["end_balance"] if rows else 0
    summary = f"按现在收支，这个月预计还能剩 {first:,.0f} 元。"
    if flags:
        summary += " " + flags[0]

    return {
        "start_month": cur,
        "start_cash": round(balance - rows[0]["net"] if rows else balance, 2),
        "months": rows,
        "flags": flags,
        "summary": summary,
    }