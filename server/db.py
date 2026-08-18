"""
SQLite 数据层：熟客档案、记忆点、交易流水（复式记账）、凭证、提醒
基于省账通（shengzhangtong）能力：大白话 → 分类映射 → 借贷凭证
分类映射等常量见 categories.py
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date

from config import DB_PATH
from categories import CATEGORY_TO_ACCOUNTS, ACCOUNT_NAMES, FRIENDLY_NAMES, detect_category


@contextmanager
def get_conn():
    """返回连接并在退出时提交+关闭，避免 Windows 下文件句柄泄漏"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            phone       TEXT DEFAULT '',
            tags        TEXT DEFAULT '',
            favorite    TEXT DEFAULT '',
            last_visit  TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
            trans_type  TEXT NOT NULL DEFAULT 'income' CHECK(trans_type IN ('income','expense')),
            category    TEXT DEFAULT '主营业务收入',
            item        TEXT DEFAULT '',
            amount      REAL DEFAULT 0,
            counterparty TEXT DEFAULT '',
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS vouchers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_no  TEXT UNIQUE NOT NULL,
            voucher_date TEXT NOT NULL,
            summary     TEXT,
            transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
            status      TEXT DEFAULT 'approved',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS voucher_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            voucher_id  INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
            account_code TEXT NOT NULL,
            account_name TEXT NOT NULL,
            direction   TEXT NOT NULL CHECK(direction IN ('debit','credit')),
            amount      REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            done        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 收款账户（双通道：微信支付商户号 / 聚合支付）
        CREATE TABLE IF NOT EXISTS payment_sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL DEFAULT 'wechat' CHECK(source_type IN ('wechat','aggregate')),
            name        TEXT DEFAULT '',
            mchid       TEXT DEFAULT '',
            appid       TEXT DEFAULT '',
            cert_path   TEXT DEFAULT '',
            private_key_path TEXT DEFAULT '',
            api_v3_key  TEXT DEFAULT '',
            enabled     INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- 账单同步日志
        CREATE TABLE IF NOT EXISTS bill_sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   INTEGER NOT NULL REFERENCES payment_sources(id) ON DELETE CASCADE,
            bill_date   TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'success' CHECK(status IN ('success','error','empty')),
            fetched     INTEGER DEFAULT 0,
            imported    INTEGER DEFAULT 0,
            skipped     INTEGER DEFAULT 0,
            error       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        """)
        # 迁移：transactions 增加 source / wx_trade_id（老库升级）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
        if "source" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN source TEXT DEFAULT 'manual'")
        if "wx_trade_id" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN wx_trade_id TEXT DEFAULT ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_wx_trade_id "
                     "ON transactions(wx_trade_id) WHERE wx_trade_id != ''")


# ---------------- 熟客 ----------------
def list_customers():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.*, COUNT(t.id) AS order_count "
            "FROM customers c LEFT JOIN transactions t ON t.customer_id=c.id "
            "GROUP BY c.id ORDER BY c.last_visit DESC").fetchall()
        return [dict(r) for r in rows]


def get_customer(cid):
    with get_conn() as conn:
        c = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if not c:
            return None
        c = dict(c)
        c["memories"] = [dict(r) for r in conn.execute(
            "SELECT * FROM memories WHERE customer_id=? ORDER BY created_at DESC", (cid,))]
        c["transactions"] = [dict(r) for r in conn.execute(
            "SELECT * FROM transactions WHERE customer_id=? ORDER BY created_at DESC LIMIT 20", (cid,))]
        return c


def find_or_create_customer(name, phone="", tags="", favorite=""):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE name=? OR (phone!='' AND phone=?)",
            (name, phone)).fetchone()
        if row:
            cid = row["id"]
            if favorite:
                conn.execute("UPDATE customers SET favorite=?, last_visit=datetime('now','localtime') WHERE id=?", (favorite, cid))
            return cid, False
        cur = conn.execute(
            "INSERT INTO customers(name, phone, tags, favorite, last_visit) VALUES(?,?,?,?,datetime('now','localtime'))",
            (name, phone, tags, favorite))
        return cur.lastrowid, True


def update_customer(cid, **fields):
    allowed = {"name", "phone", "tags", "favorite"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if sets:
        vals.append(cid)
        with get_conn() as conn:
            conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id=?", vals)


# ---------------- 记忆点 ----------------
def add_memory(customer_id, content):
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO memories(customer_id, content) VALUES(?,?)", (customer_id, content))
        return cur.lastrowid


def recent_memories(per_customer=3):
    """每位熟客最近 N 条记忆点：单次查询返回 {customer_id: [content, ...]}，避免提醒生成时 N+1 查询"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.customer_id, m.content FROM memories m "
            "JOIN customers c ON c.id=m.customer_id "
            "ORDER BY m.id DESC").fetchall()
    seen, out = {}, {}
    for r in rows:
        cid = r["customer_id"]
        if seen.get(cid, 0) < per_customer:
            out.setdefault(cid, []).append(r["content"])
            seen[cid] = seen.get(cid, 0) + 1
    return out


# ---------------- 交易 + 凭证（复式记账） ----------------
def add_transaction(customer_id, item, amount, trans_type="income", category="主营业务收入",
                    counterparty="", note=""):
    """记一笔：写交易流水；有金额时自动生成借贷凭证（省账通映射），无金额仅记流水"""
    with get_conn() as conn:
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
    with get_conn() as conn:
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
    with get_conn() as conn:
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

    with get_conn() as conn:
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


# ---------------- 提醒 ----------------
def add_reminder(customer_id, content):
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO reminders(customer_id, content) VALUES(?,?)", (customer_id, content))
        return cur.lastrowid


def list_reminders(done=None):
    with get_conn() as conn:
        sql = ("SELECT r.*, c.name AS customer_name FROM reminders r "
               "JOIN customers c ON c.id=r.customer_id ")
        if done is not None:
            sql += "WHERE r.done=?"
            rows = conn.execute(sql + " ORDER BY r.created_at DESC", (done,)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY r.created_at DESC").fetchall()
        return [dict(r) for r in rows]


def mark_reminder_done(rid, done=1):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET done=? WHERE id=?", (done, rid))


def list_transactions(year=None, month=None, limit=100):
    """月度交易流水（查账用）：含客户名与口语分类名"""
    today = date.today()
    year = year or today.year
    month = month or today.month
    period = f"{year}-{month:02d}"
    with get_conn() as conn:
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

    with get_conn() as conn:
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


# ---------------- 收款账户（微信商户 / 聚合支付） ----------------
def list_payment_sources():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM payment_sources ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_payment_source(sid):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM payment_sources WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None


def save_payment_source(source_type="wechat", name="", mchid="", appid="",
                        cert_path="", private_key_path="", api_v3_key="",
                        enabled=0, sid=None):
    """新增或更新收款账户（sid 有值则更新）"""
    with get_conn() as conn:
        if sid:
            conn.execute(
                "UPDATE payment_sources SET source_type=?, name=?, mchid=?, appid=?, "
                "cert_path=?, private_key_path=?, api_v3_key=?, enabled=? WHERE id=?",
                (source_type, name, mchid, appid, cert_path, private_key_path,
                 api_v3_key, 1 if enabled else 0, sid))
            return sid
        cur = conn.execute(
            "INSERT INTO payment_sources(source_type, name, mchid, appid, cert_path, "
            "private_key_path, api_v3_key, enabled) VALUES(?,?,?,?,?,?,?,?)",
            (source_type, name, mchid, appid, cert_path, private_key_path,
             api_v3_key, 1 if enabled else 0))
        return cur.lastrowid


def delete_payment_source(sid):
    with get_conn() as conn:
        conn.execute("DELETE FROM payment_sources WHERE id=?", (sid,))


def set_payment_source_enabled(sid, enabled):
    with get_conn() as conn:
        conn.execute("UPDATE payment_sources SET enabled=? WHERE id=?", (1 if enabled else 0, sid))


def add_sync_log(source_id, bill_date, status="success", fetched=0, imported=0,
                 skipped=0, error=""):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO bill_sync_log(source_id, bill_date, status, fetched, imported, skipped, error) "
            "VALUES(?,?,?,?,?,?,?)",
            (source_id, bill_date, status, fetched, imported, skipped, error))
        return cur.lastrowid


def list_sync_logs(limit=30):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT l.*, s.name AS source_name, s.source_type "
            "FROM bill_sync_log l LEFT JOIN payment_sources s ON s.id=l.source_id "
            "ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def last_sync_date(source_id):
    """该账户最近一次成功同步的账单日期（无则 None）"""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT MAX(bill_date) AS d FROM bill_sync_log "
            "WHERE source_id=? AND status IN ('success','empty')", (source_id,)).fetchone()
        return r["d"] if r else None