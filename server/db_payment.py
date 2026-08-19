"""db_payment.py — SQLite 数据层 · 收款账户 / 账单同步日志

从 db.py 拆分而来，保持 `import db; db.list_payment_sources()` 等调用不变。
连接统一走 db.py 的 get_conn（惰性导入避免循环依赖）。
"""

__all__ = [
    "list_payment_sources", "get_payment_source", "save_payment_source",
    "delete_payment_source", "set_payment_source_enabled",
    "add_sync_log", "list_sync_logs", "last_sync_date",
]


def _conn():
    from db import get_conn  # 惰性导入：db.py 聚合层加载完成后才执行，避免循环导入
    return get_conn()


# ---------------- 收款账户（微信商户 / 聚合支付） ----------------
def list_payment_sources():
    """收款账户列表（供前端展示）。api_v3_key 脱敏：有值显示 ***，不回传明文。
    内部同步流程请用 get_payment_source()（返回完整字段）。"""
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM payment_sources ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["api_v3_key"] = "***" if d.get("api_v3_key") else ""
            out.append(d)
        return out


def get_payment_source(sid):
    with _conn() as conn:
        r = conn.execute("SELECT * FROM payment_sources WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None


def save_payment_source(source_type="wechat", name="", mchid="", appid="",
                        cert_path="", private_key_path="", api_v3_key="",
                        enabled=0, sid=None):
    """新增或更新收款账户（sid 有值则更新）。

    api_v3_key 传空串或脱敏占位 *** 时保留原值（防止前端回传覆盖真实 Key）。"""
    with _conn() as conn:
        if sid:
            if api_v3_key in ("", "***"):
                cur = conn.execute(
                    "SELECT api_v3_key FROM payment_sources WHERE id=?", (sid,)).fetchone()
                if cur:
                    api_v3_key = cur["api_v3_key"]
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
    with _conn() as conn:
        conn.execute("DELETE FROM payment_sources WHERE id=?", (sid,))


def set_payment_source_enabled(sid, enabled):
    with _conn() as conn:
        conn.execute("UPDATE payment_sources SET enabled=? WHERE id=?", (1 if enabled else 0, sid))


# ---------------- 账单同步日志 ----------------
def add_sync_log(source_id, bill_date, status="success", fetched=0, imported=0,
                 skipped=0, error=""):
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO bill_sync_log(source_id, bill_date, status, fetched, imported, skipped, error) "
            "VALUES(?,?,?,?,?,?,?)",
            (source_id, bill_date, status, fetched, imported, skipped, error))
        return cur.lastrowid


def list_sync_logs(limit=30):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT l.*, s.name AS source_name, s.source_type "
            "FROM bill_sync_log l LEFT JOIN payment_sources s ON s.id=l.source_id "
            "ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def last_sync_date(source_id):
    """该账户最近一次成功同步的账单日期（无则 None）"""
    with _conn() as conn:
        r = conn.execute(
            "SELECT MAX(bill_date) AS d FROM bill_sync_log "
            "WHERE source_id=? AND status IN ('success','empty')", (source_id,)).fetchone()
        return r["d"] if r else None