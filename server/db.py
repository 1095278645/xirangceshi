"""
SQLite 数据层：熟客档案、记忆点、交易流水（复式记账）、凭证、提醒
基于省账通（shengzhangtong）能力：大白话 → 分类映射 → 借贷凭证
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date

from config import DB_PATH

# ===== 大白话分类 → 借方/贷方科目（小企业会计准则，来自省账通） =====
CATEGORY_TO_ACCOUNTS = {
    # 支出类（借=费用科目，贷=银行存款）
    "进货": ("5401", "100201", "进货"),
    "业务招待费": ("560103", "100201", "请客吃饭"),
    "办公费": ("560101", "100201", "日常办公"),
    "快递物流费": ("560112", "100201", "寄收快递"),
    "租赁及物业费": ("560104", "100201", "房租水电"),
    "差旅费": ("560102", "100201", "出差交通"),
    "车辆使用费": ("560105", "100201", "车子花销"),
    "广告宣传费": ("560109", "100201", "广告推广"),
    "软件服务费": ("560111", "100201", "买软件"),
    "培训费": ("560110", "100201", "学习培训"),
    "职工薪酬": ("560106", "2211", "发工资"),
    # 收入类（借=银行存款，贷=收入科目）
    "主营业务收入": ("1001", "5001", "卖东西收的钱"),
    "其他收入": ("1001", "5051", "其他收入"),
}

ACCOUNT_NAMES = {
    "1001": "库存现金", "100201": "银行存款-基本户", "2211": "应付职工薪酬",
    "5001": "主营业务收入", "5051": "其他业务收入",
    "5401": "主营业务成本", "560101": "管理费用-办公费", "560102": "管理费用-差旅费",
    "560103": "管理费用-业务招待费", "560104": "管理费用-租赁及物业费",
    "560105": "管理费用-车辆使用费", "560106": "管理费用-职工薪酬",
    "560109": "管理费用-广告宣传费", "560110": "管理费用-培训费",
    "560111": "管理费用-软件服务费", "560112": "管理费用-快递物流费",
}

# 关键词 → 分类（无 API Key 时的兜底映射）
KEYWORD_TO_CATEGORY = [
    (("饭", "请客", "喝酒", "聚餐", "招待"), "业务招待费"),
    (("进", "采购", "批发", "拿货", "补货"), "进货"),
    (("咖啡", "打印", "文具", "纸", "笔", "办公"), "办公费"),
    (("快递", "物流", "邮"), "快递物流费"),
    (("房租", "物业", "水费", "电费", "燃气"), "租赁及物业费"),
    (("车", "油", "加油", "停车", "过路"), "车辆使用费"),
    (("机票", "酒店", "高铁", "出差"), "差旅费"),
    (("广告", "推广", "传单"), "广告宣传费"),
    (("软件", "会员", "订阅", "云"), "软件服务费"),
    (("培训", "课程", "学费"), "培训费"),
    (("工资", "发薪", "社保"), "职工薪酬"),
]

FRIENDLY_NAMES = {k: v[2] for k, v in CATEGORY_TO_ACCOUNTS.items()}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
        """)


# ---------------- 分类工具 ----------------
def detect_category(text: str) -> tuple:
    """关键词兜底分类：返回 (分类, 收支类型)"""
    for keywords, category in KEYWORD_TO_CATEGORY:
        if any(k in text for k in keywords):
            return category, ("expense" if category != "其他收入" else "income")
    return "主营业务收入", "income"


# ---------------- 熟客 ----------------
def list_customers():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM transactions t WHERE t.customer_id=c.id) AS order_count "
            "FROM customers c ORDER BY c.last_visit DESC").fetchall()
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


# ---------------- 交易 + 凭证（复式记账） ----------------
def add_transaction(customer_id, item, amount, trans_type="income", category="主营业务收入",
                    counterparty="", note=""):
    """记一笔：写交易流水 + 自动生成借贷凭证（省账通映射）"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO transactions(customer_id, item, amount, trans_type, category, counterparty, note) "
            "VALUES(?,?,?,?,?,?,?)",
            (customer_id, item, amount, trans_type, category, counterparty, note))
        txn_id = cur.lastrowid
        if customer_id:
            conn.execute("UPDATE customers SET last_visit=datetime('now','localtime') WHERE id=?", (customer_id,))
        voucher = _auto_voucher(conn, txn_id, amount, trans_type, category, item, counterparty)
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
    voucher_no = f"记-{seq:03d}"

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
        out = []
        for r in rows:
            d = dict(r)
            d["entries"] = [dict(x) for x in conn.execute(
                "SELECT * FROM voucher_entries WHERE voucher_id=?", (r["id"],))]
            out.append(d)
        return out


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