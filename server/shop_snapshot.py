"""shop_snapshot.py — 掌柜的全店经营快照

## 为什么需要

原先的「每日复盘」只是把三个数字拼成一句话：
`今天收 X / 本月收 Y / 单店一句话`。它读的是**账本 + 单店模型**，
而这家店的其他经营动作 —— 熟客、库存、发票、赊账、预算、税 —— 全都没进掌柜的眼睛。
于是"掌柜"退化成一块只显示收支的仪表盘。

真正的掌柜应该**知晓全店全流程的经营动作**。这个模块负责把散落在各个域的事实
汇总成一份**带数字的快照文本**，喂给多 agent 团队（员工各管一摊 → 掌柜裁决）。

## 设计要求

- **只陈述事实，不给建议**：建议是员工和掌柜的事。快照越干净，"谁在臆测"越容易看出来。
- **缺失要说出来**：库存没登记就写"还没建库存档案"，不要用 0 冒充 ——
  0 和"没数据"对掌柜是两个完全不同的信号（员工据此提的动作也不同）。
- **控制篇幅**：整份快照目标 600~900 字，塞太满会让员工的产出变泛。
- 纯本地计算，不调 AI、不联网，便于测试与无 Key 降级。
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("shop_snapshot")

__all__ = ["build_snapshot", "snapshot_facts"]


def _f(v, nd: int = 0) -> str:
    try:
        return f"{float(v):,.{nd}f}"
    except (TypeError, ValueError):
        return "0"


def _line(tag: str, text: str) -> str:
    return f"[{tag}] {text}"


# ---------------- 各域事实 ----------------

def _money_facts(month: str) -> list[str]:
    from db import today_summary, monthly_summary, store_ledger_stats
    today = today_summary()
    m = monthly_summary()
    out = [_line("今日",
                 f"收 {_f(today['income'])} 元、支 {_f(today['expense'])} 元、"
                 f"{today['cnt']} 笔、净 {_f(today['balance'])} 元")]
    # 月度分类：看谁在涨、谁是大头
    cats = m.get("categories") or []
    if cats:
        # 注意字段名是 total（不是 amount）—— 猜错过一次，被探针抓到
        top = sorted(cats, key=lambda c: abs(c.get("total") or 0), reverse=True)[:3]
        desc = "、".join(
            f"{c.get('category') or c.get('name')} {_f(c.get('total'))} 元"
            for c in top)
        out.append(_line("本月", f"收 {_f(m['income'])} / 支 {_f(m['expense'])} 元"
                                f"（{m.get('period') or month}），"
                                f"{m.get('income_cnt', 0) + m.get('expense_cnt', 0)} 笔；"
                                f"金额最大的三类：{desc}"))
    else:
        out.append(_line("本月", f"收 {_f(m['income'])} / 支 {_f(m['expense'])} 元"
                                f"（{m.get('period') or month}）"))
    try:
        s = store_ledger_stats()
        if s.get("daily_revenue") is not None:
            gm = s.get("gross_margin")
            gm_txt = f"，毛利率约 {round(gm * 100)}%" if gm else ""
            out.append(_line("经营水平",
                             f"最近有营业的 {s['period']} 月日均流水 "
                             f"{_f(s['daily_revenue'])} 元{gm_txt}"
                             f"（共 {s.get('active_days') or '?'} 天有流水）"))
    except Exception as e:  # noqa: BLE001
        log.warning("账本反推失败（快照省略该段）：%s", e)
    return out


def _customer_facts() -> list[str]:
    from db import list_customers
    try:
        rows = list_customers()
    except Exception as e:  # noqa: BLE001
        log.warning("熟客读取失败（快照省略该段）：%s", e)
        return []
    if not rows:
        return [_line("熟客", "还没有熟客档案")]
    # 只看"该来没来"的：按上次到店排序（最久没来的在前）
    def _lv(c):
        return (c.get("last_visit") or "")
    ordered = sorted(rows, key=_lv)
    stale = [c for c in ordered if c.get("last_visit")][:3]
    names = "、".join(
        f"{c['name']}（上次 {c.get('last_visit') or '未知'}，"
        f"来过 {c.get('order_count') or 0} 次）" for c in stale)
    return [_line("熟客", f"共 {len(rows)} 位建档；最久没来的：{names or '暂无记录'}")]


def _stock_facts() -> list[str]:
    from db import stock_summary
    try:
        s = stock_summary()
    except Exception as e:  # noqa: BLE001
        log.warning("库存读取失败（快照省略该段）：%s", e)
        return []
    n = s.get("total_items") or 0
    if not n:
        return [_line("库存", "还没建立库存档案（进货/出货没登记过）")]
    out = [_line("库存", f"{n} 种货，货值约 {_f(s.get('total_value'))} 元")]
    low = s.get("low_stock") or []
    if low:
        names = "、".join(f"{p.get('name')}（剩 {p.get('stock_qty')}）" for p in low[:3])
        out.append(_line("待补货", f"{len(low)} 种见底：{names}"))
    exp = s.get("expiring") or []
    if exp:
        names = "、".join(f"{p.get('name')}（{p.get('expiry_date')}）" for p in exp[:3])
        out.append(_line("临期", f"{len(exp)} 种快过期：{names}"))
    return out


def _cash_facts(month: str) -> list[str]:
    from db import list_budgets, list_debts
    out = []
    try:
        debts = list_debts()
        open_debts = [d for d in debts if (d.get("status") or "open") == "open"]
        if open_debts:
            recv = [d for d in open_debts if d.get("kind") == "receivable"]
            pay = [d for d in open_debts if d.get("kind") == "payable"]
            overdue = [d for d in open_debts
                       if (d.get("due_date") or "9999") < date.today().isoformat()]
            out.append(_line("赊账",
                             f"别人欠我 {len(recv)} 笔共 {_f(sum(x.get('balance') or 0 for x in recv))} 元；"
                             f"我欠别人 {len(pay)} 笔共 {_f(sum(x.get('balance') or 0 for x in pay))} 元"
                             + (f"；其中 {len(overdue)} 笔已过期" if overdue else "")))
            if overdue:
                names = "、".join(
                    f"{d.get('party') or d.get('counterparty') or '未记名'}"
                    f"（{_f(d.get('balance'))} 元，{d.get('due_date')} 到期）"
                    for d in overdue[:3])
                out.append(_line("已逾期", names))
        else:
            out.append(_line("赊账", "没有挂账"))
    except Exception as e:  # noqa: BLE001
        log.warning("赊账读取失败（快照省略该段）：%s", e)

    try:
        buds = list_budgets(month)
        if buds:
            used = "、".join(
                f"{b.get('category') or '总预算'} {_f(b.get('amount'))} 元"
                for b in buds[:4])
            out.append(_line("本月预算", f"已设 {len(buds)} 项：{used}"))
        else:
            out.append(_line("本月预算", "没设预算"))
    except Exception as e:  # noqa: BLE001
        log.warning("预算读取失败（快照省略该段）：%s", e)
    return out


def _invoice_facts(month: str) -> list[str]:
    from db import invoice_summary
    try:
        s = invoice_summary(month)
    except Exception as e:  # noqa: BLE001
        log.warning("发票读取失败（快照省略该段）：%s", e)
        return []
    by_kind = {x.get("kind"): x for x in (s.get("by_kind") or [])}
    total_cnt = sum(int(k.get("cnt") or 0) for k in by_kind.values())
    if not by_kind or total_cnt == 0:
        # 一张都没登记时说"还没登记"，不要说"开出去 0 张"——后者像是有票却记错了
        return [_line("发票", "这个月还没登记发票")]
    out = []
    for kind, label in (("out", "开出去"), ("in", "收进来")):
        k = by_kind.get(kind)
        if k and int(k.get("cnt") or 0) > 0:
            out.append(_line("发票", f"{label} {k.get('cnt', 0)} 张、"
                                     f"合计 {_f(k.get('total'))} 元"))
    return out


def _tax_facts() -> list[str]:
    """报税日历：只报"最近要办的"，不堆整年。"""
    try:
        import tax as taxcalc
        cal = taxcalc.get_filing_calendar(date.today().year, date.today().month)
    except Exception as e:  # noqa: BLE001
        log.warning("报税日历失败（快照省略该段）：%s", e)
        return []
    # get_filing_calendar 返回 dict（含 items/月份键），把可能的形状都兜住
    items = []
    if isinstance(cal, dict):
        items = cal.get("items") or cal.get("reminders") or []
        if not items:
            for v in cal.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    items = v
                    break
    elif isinstance(cal, list):
        items = cal
    if not items:
        return []
    # 只保留真带内容的条目，否则会拼出"近期要办：；"这种空话
    descs = []
    for i in items:
        if not isinstance(i, dict):
            continue
        name = (i.get("name") or i.get("title") or i.get("desc") or "").strip()
        when = str(i.get("date") or i.get("day") or "").strip()
        text = f"{when} {name}".strip()
        if text:
            descs.append(text)
        if len(descs) >= 2:
            break
    return [_line("报税", f"近期要办：{'；'.join(descs)}")] if descs else []


def _correction_facts() -> list[str]:
    """更正动作：掌柜应当知道账被动过（这也是"全流程"的一部分）。"""
    from db import list_transaction_audits
    try:
        rows = list_transaction_audits(limit=5)
    except Exception as e:  # noqa: BLE001
        log.warning("审计读取失败（快照省略该段）：%s", e)
        return []
    if not rows:
        return []
    acts = {"edit": "改过金额", "void": "作废过", "refund": "退货冲销过"}
    desc = "；".join(
        f"{acts.get(r.get('action'), r.get('action'))}"
        f"{(r.get('reason') or '').strip() and '（' + (r.get('reason') or '').strip()[:20] + '）' or ''}"
        for r in rows[:3])
    return [_line("账目更正", f"最近有 {len(rows)} 次：{desc}")]


def snapshot_facts(month: str | None = None) -> dict[str, list[str]]:
    """按域返回事实（dict 便于测试逐域断言，也便于前端分块展示）。"""
    m = month or date.today().strftime("%Y-%m")
    return {
        "money": _money_facts(m),
        "customers": _customer_facts(),
        "stock": _stock_facts(),
        "cash": _cash_facts(m),
        "invoice": _invoice_facts(m),
        "tax": _tax_facts(),
        "corrections": _correction_facts(),
    }


def build_snapshot(month: str | None = None) -> str:
    """拼成给员工/掌柜看的快照文本。任何一段失败都只丢那一段，不影响其余。"""
    m = month or date.today().strftime("%Y-%m")
    parts: list[str] = []
    for _domain, lines in snapshot_facts(m).items():
        # 空行（如没建库存）也要保留：那是"这块没数据"的信号
        parts.extend(lines)
    return "\n".join(parts)
