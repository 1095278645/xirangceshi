"""SQLite 数据层：熟客档案、记忆点、交易流水、提醒"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import DB_PATH


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
            tags        TEXT DEFAULT '',          -- 逗号分隔，如 "常客,爱喝豆浆"
            favorite    TEXT DEFAULT '',          -- 常点商品
            last_visit  TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,             -- 记忆点："孙子考了一百分"
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
            item        TEXT DEFAULT '',
            amount      REAL DEFAULT 0,
            note        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            content     TEXT NOT NULL,
            done        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        """)


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


# ---------------- 交易 ----------------
def add_transaction(customer_id, item, amount, note=""):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO transactions(customer_id, item, amount, note) VALUES(?,?,?,?)",
            (customer_id, item, amount, note))
        conn.execute("UPDATE customers SET last_visit=datetime('now','localtime') WHERE id=?", (customer_id,))
        return cur.lastrowid


def today_summary():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total, COUNT(*) AS cnt FROM transactions "
            "WHERE date(created_at)=date('now','localtime')").fetchone()
        return dict(row)


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