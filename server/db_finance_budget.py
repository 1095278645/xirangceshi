"""db_finance_budget.py — 预算管理（从 db_finance.py 拆出）

预算 CRUD + 预算 vs 实际差异分析。
依赖方向：db_finance_budget → db.get_conn（惰性导入）。
"""
from __future__ import annotations

from datetime import date, datetime

__all__ = [
    "save_budget", "list_budgets", "delete_budget", "budget_vs_actual",
    "_month_expense", "_month_income",
]


def _conn():
    from db import get_conn
    return get_conn()


def save_budget(month: str, scope: str = "expense", amount: float = 0,
                category: str = "", note: str = "", bid: int | None = None):
    """新增/更新某月预算；scope=expense 支出预算 / income 收入目标。返回 id"""
    with _conn() as conn:
        if bid:
            conn.execute(
                "UPDATE budgets SET month=?, scope=?, amount=?, category=?, note=? "
                "WHERE id=?",
                (month, scope, amount, category, note, bid))
            return bid
        cur = conn.execute(
            "INSERT INTO budgets(month, scope, amount, category, note) "
            "VALUES(?,?,?,?,?)",
            (month, scope, amount, category, note))
        return cur.lastrowid


def list_budgets(month: str | None = None):
    """列出预算；可按月过滤，按月份/作用域倒序"""
    sql = "SELECT * FROM budgets"
    where, params = [], []
    if month:
        where.append("month=?")
        params.append(month)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY month DESC, id DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def delete_budget(bid: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM budgets WHERE id=?", (bid,))
        return cur.rowcount > 0


def _month_expense(conn, month: str) -> float:
    """某月实际支出（transactions, trans_type=expense）"""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM transactions "
        "WHERE trans_type='expense' AND substr(created_at,1,7)=?",
        (month,)).fetchone()
    return float(row["s"] or 0)


def _month_income(conn, month: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM transactions "
        "WHERE trans_type='income' AND substr(created_at,1,7)=?",
        (month,)).fetchone()
    return float(row["s"] or 0)


def budget_vs_actual(month: str):
    """预算 vs 实际差异（亲民：每月计划花多少、实际花了多少、超没超）。"""
    with _conn() as conn:
        budgets = conn.execute(
            "SELECT * FROM budgets WHERE month=? ORDER BY id", (month,)).fetchall()
    plans = {"income": [], "expense": []}
    total_plan = {"income": 0.0, "expense": 0.0}
    for b in budgets:
        s = b["scope"]
        d = {"category": b["category"] or "全部", "amount": b["amount"], "note": b["note"]}
        plans.setdefault(s, []).append(d)
        total_plan[s] = total_plan.get(s, 0.0) + float(b["amount"] or 0)
    with _conn() as conn:
        actual_exp = _month_expense(conn, month)
        actual_inc = _month_income(conn, month)
    actual = {"income": actual_inc, "expense": actual_exp}
    diff = {
        "income": actual_inc - total_plan["income"],
        "expense": actual_exp - total_plan["expense"],
    }
    over = []
    if total_plan["expense"] > 0:
        if actual_exp > total_plan["expense"]:
            over.append(f"这个月计划花 {total_plan['expense']:,.0f} 元，"
                        f"实际花了 {actual_exp:,.0f} 元，超了 {actual_exp - total_plan['expense']:,.0f} 元，"
                        f"得看看花哪了。")
        else:
            over.append(f"这个月计划花 {total_plan['expense']:,.0f} 元，"
                        f"实际花了 {actual_exp:,.0f} 元，没超，稳。")
    if total_plan["income"] > 0 and actual_inc < total_plan["income"]:
        over.append(f"这个月计划进账 {total_plan['income']:,.0f} 元，"
                    f"实际只进 {actual_inc:,.0f} 元，差 {total_plan['income'] - actual_inc:,.0f} 元。")
    return {
        "month": month,
        "budgets": plans,
        "plan_total": total_plan,
        "actual": {k: round(v, 2) for k, v in actual.items()},
        "diff": {k: round(v, 2) for k, v in diff.items()},
        "flags": over,
        "summary": " ".join(over) if over else "这个月还没设预算，设一下好心里有底。",
    }
