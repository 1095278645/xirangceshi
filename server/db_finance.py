"""db_finance.py — SQLite 数据层 · 资金健康（预算 / 应收应付 / 现金流取数）

对标高星财务开源项目：
  - budgets 预算：Firefly III 的预算编制 + 预算 vs 实际差异（亲民口径：计划 vs 实花）
  - debts 应收应付：Akaunting 的应收应付台账，按到期日分账龄（亲民口径：谁欠我钱 / 我欠谁钱）
  - 现金流预测取数：读取近期流水均线 + 应收应付到期计划，喂给 finance.forecast_cashflow
连接统一走 db.py 的 get_conn（惰性导入避免循环依赖），调用方式与 db_customers 等一致。
"""
from __future__ import annotations

from datetime import date, datetime

from finance import forecast_cashflow, month_key, shift_month

# 向后兼容：re-export 预算函数（调用方仍可用 db.save_budget 等）
from db_finance_budget import (  # noqa: F401
    save_budget, list_budgets, delete_budget, budget_vs_actual,
    _month_expense, _month_income,
)

__all__ = [
    # 预算（re-exported from db_finance_budget）
    "save_budget", "list_budgets", "delete_budget", "budget_vs_actual",
    # 应收应付
    "add_debt", "list_debts", "settle_debt", "delete_debt", "aging_summary",
    "debt_month_flows",
    # 现金流
    "monthly_history", "cashflow_forecast",
]


def _conn():
    from db import get_conn  # 惰性导入：db.py 聚合层加载完成后才执行
    return get_conn()


# ---------------- 应收应付（赊账台账） ----------------
def add_debt(party: str, kind: str, amount: float, due_date: str = "",
             note: str = "", did: int | None = None):
    """新增/更新一条应收应付；kind: receivable 应收 / payable 应付"""
    with _conn() as conn:
        if did:
            conn.execute(
                "UPDATE debts SET party=?, kind=?, amount=?, due_date=?, note=? WHERE id=?",
                (party, kind, amount, due_date, note, did))
            return did
        cur = conn.execute(
            "INSERT INTO debts(party, kind, amount, balance, due_date, note) "
            "VALUES(?,?,?,?,?,?)",
            (party, kind, amount, amount, due_date, note))
        return cur.lastrowid


def list_debts(kind: str | None = None, status: str | None = None):
    """列出应收应付；kind=receivable/payable，status=open/settled"""
    sql = "SELECT * FROM debts"
    where, params = [], []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if status:
        where.append("status=?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def settle_debt(did: int, settle_amount: float | None = None):
    """结清应收应付：默认全额结清；传剩余金额则部分结清，结清至 balance=0 状态置 settled"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM debts WHERE id=?", (did,)).fetchone()
        if not row:
            return None
        balance = float(row["balance"] or 0)
        if settle_amount is None:
            settle_amount = balance
        settle = min(max(float(settle_amount), 0), balance)
        new_balance = round(balance - settle, 2)
        status = "settled" if new_balance <= 0 else "open"
        conn.execute(
            "UPDATE debts SET balance=?, status=? WHERE id=?",
            (new_balance, status, did))
    d = dict(row)
    d["balance"] = new_balance
    d["status"] = status
    return d


def delete_debt(did: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM debts WHERE id=?", (did,))
        return cur.rowcount > 0


def _aging_bucket(days: int) -> str:
    if days < 0:
        return "未到期"
    if days <= 30:
        return "0-30天"
    if days <= 60:
        return "31-60天"
    if days <= 90:
        return "61-90天"
    return "90天以上"


def aging_summary():
    """应收应付账龄汇总（亲民：欠多久了、该不该催）。
    按 overdue 天数分桶：未到期 / 0-30 / 31-60 / 61-90 / 90以上。
    90 天以上的应收会提示「这笔钱欠了三个月，得去催」"""
    today = date.today()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM debts WHERE status='open' ORDER BY id DESC").fetchall()
    result = {"receivable": [], "payable": [], "flags": []}
    for r in rows:
        d = dict(r)
        due = d.get("due_date") or ""
        if due:
            try:
                due_dt = datetime.strptime(due[:10], "%Y-%m-%d").date()
            except ValueError:
                due_dt = None
        else:
            due_dt = None
        if due_dt is None:
            # 无到期日按创建日推 30 天宽限
            created = (d.get("created_at") or "")[:10]
            try:
                due_dt = datetime.strptime(created, "%Y-%m-%d").date().fromordinal(
                    datetime.strptime(created, "%Y-%m-%d").date().toordinal() + 30)
            except ValueError:
                due_dt = today
        overdue_days = (today - due_dt).days
        d["aging_bucket"] = _aging_bucket(overdue_days)
        d["overdue_days"] = overdue_days
        result[d["kind"]].append(d)
        # 亲民催收提醒
        if d["kind"] == "receivable" and overdue_days > 60 and d["balance"] > 0:
            result["flags"].append(
                f"{d['party']} 还欠 {d['balance']:,.0f} 元，欠了 {overdue_days} 天了，"
                f"该去催一催。")
        elif d["kind"] == "payable" and overdue_days > 30 and d["balance"] > 0:
            result["flags"].append(
                f"你欠 {d['party']} {d['balance']:,.0f} 元，拖了 {overdue_days} 天，"
                f"别影响关系，早点结。")
    return result


def debt_month_flows(months: int = 6, start_month: str | None = None) -> list[dict]:
    """应收应付到期对现金流的月度影响（正=收回，负=支出）。
    供现金流预测叠加到期收付。"""
    from datetime import date as _date
    today = _date.today()
    cur = start_month or month_key(today)
    plan: dict[str, float] = {}
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM debts WHERE status='open' ORDER BY id").fetchall()
    for r in rows:
        d = dict(r)
        due = (d.get("due_date") or "")[:10]
        try:
            due_dt = datetime.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            due_dt = None
        if due_dt is None:
            continue
        m = month_key(due_dt)
        if m < cur or m > shift_month(cur, months - 1):
            continue
        bal = float(d.get("balance") or 0)
        net = bal if d["kind"] == "receivable" else -bal
        plan[m] = plan.get(m, 0.0) + net
    return [{"month": m, "net": round(plan.get(m, 0.0), 2)}
            for m in (shift_month(cur, i) for i in range(max(1, months)))]


# ---------------- 现金流预测取数 ----------------
def monthly_history(months: int = 3) -> dict:
    """近 N 个月实际收支月均线（用于现金流预测基准）"""
    today = date.today()
    cur = month_key(today)
    income_sum = expense_sum = 0.0
    count = 0
    with _conn() as conn:
        for i in range(int(months)):
            m = shift_month(cur, -1 - i)   # 从上一月往前推 N 个月
            count += 1
            income_sum += _month_income(conn, m)
            expense_sum += _month_expense(conn, m)
    n = max(count, 1)
    return {
        "base_income": round(income_sum / n, 2),
        "base_expense": round(expense_sum / n, 2),
        "lookback": int(months),
        "sample_months": count,
    }


def cashflow_forecast(cash_on_hand: float = 0, months: int = 6,
                      safety_buffer: float = 0):
    """现金流滚动预测入口：取账本月均线 + 应收应付到期计划 → forecast_cashflow"""
    hist = monthly_history()
    flows = debt_month_flows(months)
    return forecast_cashflow(
        cash_on_hand=cash_on_hand,
        base_income=hist["base_income"],
        base_expense=hist["base_expense"],
        debt_flows=flows,
        months=months,
        safety_buffer=safety_buffer,
    )