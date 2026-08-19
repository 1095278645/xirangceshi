"""db_customers.py — SQLite 数据层 · 熟客 / 记忆点 / 提醒

从 db.py 拆分而来，保持 `import db; db.list_customers()` 等调用不变。
连接统一走 db.py 的 get_conn（惰性导入避免循环依赖）。
"""

__all__ = [
    "list_customers", "get_customer", "find_or_create_customer", "update_customer",
    "add_memory", "recent_memories",
    "add_reminder", "list_reminders", "mark_reminder_done",
]


def _conn():
    from db import get_conn  # 惰性导入：db.py 聚合层加载完成后才执行，避免循环导入
    return get_conn()


# ---------------- 熟客 ----------------
def list_customers():
    with _conn() as conn:
        rows = conn.execute(
            "SELECT c.*, COUNT(t.id) AS order_count "
            "FROM customers c LEFT JOIN transactions t ON t.customer_id=c.id "
            "GROUP BY c.id ORDER BY c.last_visit DESC").fetchall()
        return [dict(r) for r in rows]


def get_customer(cid):
    with _conn() as conn:
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
    with _conn() as conn:
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
        with _conn() as conn:
            conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id=?", vals)


# ---------------- 记忆点 ----------------
def add_memory(customer_id, content):
    with _conn() as conn:
        cur = conn.execute("INSERT INTO memories(customer_id, content) VALUES(?,?)", (customer_id, content))
        return cur.lastrowid


def recent_memories(per_customer=3):
    """每位熟客最近 N 条记忆点：单次查询返回 {customer_id: [content, ...]}，避免提醒生成时 N+1 查询"""
    with _conn() as conn:
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


# ---------------- 提醒 ----------------
def add_reminder(customer_id, content):
    with _conn() as conn:
        cur = conn.execute("INSERT INTO reminders(customer_id, content) VALUES(?,?)", (customer_id, content))
        return cur.lastrowid


def list_reminders(done=None):
    with _conn() as conn:
        sql = ("SELECT r.*, c.name AS customer_name FROM reminders r "
               "JOIN customers c ON c.id=r.customer_id ")
        if done is not None:
            sql += "WHERE r.done=?"
            rows = conn.execute(sql + " ORDER BY r.created_at DESC", (done,)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY r.created_at DESC").fetchall()
        return [dict(r) for r in rows]


def mark_reminder_done(rid, done=1):
    with _conn() as conn:
        conn.execute("UPDATE reminders SET done=? WHERE id=?", (done, rid))