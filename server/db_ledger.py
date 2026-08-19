"""db_ledger.py — SQLite 数据层 · 交易流水 / 借贷凭证 / 收支汇总 / 单店反推

从 db.py 拆分而来，保持 `import db; db.add_transaction()` 等调用不变。
连接统一走 db.py 的 get_conn（惰性导入避免循环依赖）。
"""
from datetime import date

from categories import CATEGORY_TO_ACCOUNTS, ACCOUNT_NAMES, FRIENDLY_NAMES

__all__ = [
    "add_transaction", "_auto_voucher", "list_vouchers",
    "today_summary", "monthly_summary", "list_transactions",
    "store_ledger_stats",
]


def _conn():
    from db import get_conn  # 惰性导入：db.py 聚合层加载完成后才执行，避免循环导入
    return get_conn()


# ---------------- 交易 + 凭证（复式记账） ----------------
def add_transaction(customer_id, item, amount, trans_type="income", category="主营业务收入",
                    counterparty="", note=""):
    """记一笔：写交易流水；有金额时自动生成借贷凭证（省账通映射），无金额仅记流水"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO transactions(customer_id, item, amount, trans_type, category, counterparty, note) "
            "VALUES(?,?,?,?,?,?,?)",
            (customer_id, item, amount if amount is not None else 0, trans_type, category, counterparty, note))
        txn_id = cur.lastrowid
        if customer_id:
            conn.execute("UPDATE customers SET last_visit=datetime('now','localtime') WHERE id=?", (customer_id,))
        voucher = _auto_voucher(conn, txn_id, amount, trans_type, category, item, counterparty) if amount else None
        return txn_id, voucher


def _auto_voucher(conn, txn_id, amount, trans_type, category, summary, counterparty=""):
    """自动生成借贷凭证：借/贷两条分录，保证借贷平衡"""
    mapping = CATEGORY_TO_ACCOUNTS.get(category)
    if not mapping:
        # 未知分类：兜底走主营业务收入或办公费
        mapping = CATEGORY_TO_ACCOUNTS["主营业务收入"] if trans_type == "income" else CATEGORY_TO_ACCOUNTS["办公费"]
    debit_code, credit_code, _friendly = mapping

    today = date.today().isoformat()
    period = today[:7]
    seq = conn.execute(
        "SELECT COUNT(*) FROM vouchers WHERE voucher_date LIKE ?", (period + "%",)
    ).fetchone()[0] + 1
    # 凭证号带月份前缀：voucher_no 列全局 UNIQUE，若每月从 1 重新编号会在跨月时撞号
    voucher_no = f"记-{period.replace('-', '')}-{seq:03d}"

    cur = conn.execute(
        "INSERT INTO vouchers(voucher_no, voucher_date, summary, transaction_id) VALUES(?,?,?,?)",
        (voucher_no, today, summary, txn_id))
    vid = cur.lastrowid

    conn.execute(
        "INSERT INTO voucher_entries(voucher_id, account_code, account_name, direction, amount) VALUES(?,?,?,?,?)",
        (vid, debit_code, ACCOUNT_NAMES.get(debit_code, debit_code), "debit", amount))
    conn.execute(
        "INSERT INTO voucher_entries(voucher_id, account_code, account_name, direction, amount) VALUES(?,?,?,?,?)",
        (vid, credit_code, ACCOUNT_NAMES.get(credit_code, credit_code), "credit", amount))

    return {"voucher_no": voucher_no, "debit": ACCOUNT_NAMES.get(debit_code, debit_code),
            "credit": ACCOUNT_NAMES.get(credit_code, credit_code)}


def list_vouchers(limit=50):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT v.*, t.item, t.amount, t.trans_type, t.category "
            "FROM vouchers v LEFT JOIN transactions t ON t.id=v.transaction_id "
            "ORDER BY v.id DESC LIMIT ?", (limit,)).fetchall()
        vids = [r["id"] for r in rows]
        by_vid = {}
        if vids:
            ph = ",".join("?" * len(vids))
            entries = conn.execute(
                f"SELECT * FROM voucher_entries WHERE voucher_id IN ({ph}) ORDER BY voucher_id, id",
                vids).fetchall()
            for e in entries:
                by_vid.setdefault(e["voucher_id"], []).append(dict(e))
        return [dict(r) | {"entries": by_vid.get(r["id"], [])} for r in rows]


def today_summary():
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN trans_type='income' THEN amount ELSE 0 END),0) AS income, "
            "COALESCE(SUM(CASE WHEN trans_type='expense' THEN amount ELSE 0 END),0) AS expense, "
            "COUNT(*) AS cnt FROM transactions WHERE date(created_at)=date('now','localtime')").fetchone()
        d = dict(row)
        d["balance"] = round(d["income"] - d["expense"], 2)
        return d


def monthly_summary(year=None, month=None):
    """月度收支汇总 + 分类明细（省账通查账能力）"""
    today = date.today()
    year = year or today.year
    month = month or today.month
    period = f"{year}-{month:02d}"

    with _conn() as conn:
        rows = conn.execute(
            "SELECT trans_type, SUM(amount) AS total, COUNT(*) AS cnt FROM transactions "
            "WHERE substr(created_at,1,7)=? GROUP BY trans_type", (period,)).fetchall()
        income = expense = 0
        income_cnt = expense_cnt = 0
        for r in rows:
            if r["trans_type"] == "income":
                income, income_cnt = r["total"] or 0, r["cnt"]
            else:
                expense, expense_cnt = r["total"] or 0, r["cnt"]

        cats = conn.execute(
            "SELECT category, trans_type, SUM(amount) AS total, COUNT(*) AS cnt FROM transactions "
            "WHERE substr(created_at,1,7)=? GROUP BY category, trans_type ORDER BY total DESC",
            (period,)).fetchall()
        categories = [{
            "category": r["category"],
            "friendly": FRIENDLY_NAMES.get(r["category"], r["category"]),
            "trans_type": r["trans_type"],
            "total": r["total"],
            "cnt": r["cnt"],
        } for r in cats]

        return {
            "period": period,
            "income": round(income, 2), "income_cnt": income_cnt,
            "expense": round(expense, 2), "expense_cnt": expense_cnt,
            "balance": round(income - expense, 2),
            "categories": categories,
        }


def list_transactions(year=None, month=None, limit=100):
    """月度交易流水（查账用）：含客户名与口语分类名"""
    today = date.today()
    year = year or today.year
    month = month or today.month
    period = f"{year}-{month:02d}"
    with _conn() as conn:
        rows = conn.execute(
            "SELECT t.*, c.name AS customer_name "
            "FROM transactions t LEFT JOIN customers c ON c.id=t.customer_id "
            "WHERE substr(t.created_at,1,7)=? ORDER BY t.id DESC LIMIT ?",
            (period, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["friendly"] = FRIENDLY_NAMES.get(d["category"], d["category"])
            out.append(d)
        return out


# ---------------- 单店模型：从账本流水反推 ----------------
# 「进货」→ 主营业务成本(5401)，属直接成本；房租/人工等固定成本不算在毛利率里
_COST_CATEGORIES = {"进货"}


def store_ledger_stats(year=None, month=None):
    """从账本真实流水反推单店模型输入：实际日销 + 毛利率。

    - 不传 year/month 时，自动定位「最近一个有收入流水」的月份（往前最多找 12 个月）
    - 实际日销 = 该月收入合计 ÷ 有收入流水的天数
    - 毛利率  = (收入 - 直接成本) / 收入；无直接成本记录时返回 None（用业态默认）
    """
    today = date.today()
    y = year or today.year
    m = month or today.month
    period = f"{y}-{m:02d}"

    with _conn() as conn:
        # 自动定位最近有收入的月份
        if not year and not month:
            for _ in range(12):
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM transactions "
                    "WHERE substr(created_at,1,7)=? AND trans_type='income' AND amount>0",
                    (period,)).fetchone()
                if row["c"] > 0:
                    break
                m -= 1
                if m == 0:
                    m = 12
                    y -= 1
                period = f"{y}-{m:02d}"

        inc = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total, "
            "COUNT(DISTINCT substr(created_at,1,10)) AS days "
            "FROM transactions WHERE substr(created_at,1,7)=? "
            "AND trans_type='income' AND amount>0", (period,)).fetchone()
        income_total = inc["total"] or 0
        active_days = inc["days"] or 0

        cost = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM transactions "
            "WHERE substr(created_at,1,7)=? AND trans_type='expense' AND category IN (%s)"
            % ",".join("?" * len(_COST_CATEGORIES)),
            (period, *_COST_CATEGORIES)).fetchone()
        cost_total = cost["total"] or 0

        daily_revenue = round(income_total / active_days, 1) if active_days else None
        gross_margin = (round((income_total - cost_total) / income_total, 3)
                        if income_total > 0 and cost_total > 0 else None)

        note = f"按 {period} 账本流水反推：收入 {income_total:,.0f} 元 / {active_days} 天营业"
        if cost_total > 0:
            note += f"，直接成本 {cost_total:,.0f} 元"
        else:
            note += "，暂无进货成本记录（毛利率请按实际填）"

        return {
            "period": period,
            "income_total": round(income_total, 2),
            "cost_total": round(cost_total, 2),
            "active_days": active_days,
            "daily_revenue": daily_revenue,
            "gross_margin": gross_margin,
            "note": note,
        }