"""db_invoice.py — SQLite 数据层 · 发票台账

对标 Akaunting 的发票（销项/进项）管理，为后续报税申报打基础。
掌柜口径（亲民）：店主只关心「这个月开了多少票、收了多少进项票、有没有票能抵扣」。
纯本地算法，不依赖 AI。
  - invoices：销项发票(out=开给客户) / 进项发票(in=供应商开给我)，含税额与状态
"""
from __future__ import annotations

__all__ = [
    "add_invoice", "list_invoices", "update_invoice", "void_invoice",
    "invoice_summary",
]


def _conn():
    from db import get_conn
    return get_conn()


# ---------------- 发票台账 ----------------
def add_invoice(kind: str, party: str = "", invoice_no: str = "", amount: float = 0,
                rate: float = 0, tax_amount: float = 0, issued_date: str = "",
                note: str = "", iid: int | None = None):
    """新增/更新发票；kind: out 销项(开票) / in 进项(收票)"""
    with _conn() as conn:
        if iid:
            conn.execute(
                "UPDATE invoices SET kind=?, party=?, invoice_no=?, amount=?, rate=?, "
                "tax_amount=?, issued_date=?, note=? WHERE id=?",
                (kind, party, invoice_no, amount, rate, tax_amount,
                 issued_date, note, iid))
            return iid
        cur = conn.execute(
            "INSERT INTO invoices(kind, party, invoice_no, amount, rate, "
            "tax_amount, issued_date, note) VALUES(?,?,?,?,?,?,?,?)",
            (kind, party, invoice_no, amount, rate, tax_amount, issued_date, note))
        return cur.lastrowid


def list_invoices(kind: str | None = None, status: str | None = None):
    sql = "SELECT * FROM invoices"
    where, params = [], []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if status:
        where.append("status=?")
        params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY issued_date DESC, id DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_invoice(iid: int, **fields) -> bool:
    allowed = {"kind", "party", "invoice_no", "amount", "rate",
               "tax_amount", "issued_date", "note"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return False
    params.append(iid)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE invoices SET {','.join(sets)} WHERE id=?", params)
        return cur.rowcount > 0


def void_invoice(iid: int) -> bool:
    """作废发票（红冲状态），返回是否成功"""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE invoices SET status='void' WHERE id=? AND status!='void'", (iid,))
        return cur.rowcount > 0


def invoice_summary(month: str | None = None):
    """发票台账汇总（亲民：开了多少票 / 收了 多少进项票）。
    可按 'YYYY-MM' 过滤；不传则统计全部。"""
    # 列表：按月份过滤展示全部（含作废）；聚合：仅统计 issued + 月份
    list_where, agg_where, params = "", " WHERE status='issued'", []
    if month:
        list_where = " WHERE substr(issued_date,1,7)=?"
        agg_where += " AND substr(issued_date,1,7)=?"
        params.append(month)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM invoices" + list_where + " ORDER BY issued_date DESC, id DESC",
            params).fetchall()
        # by_kind 恒含 out/in 两行（无数据也补 0 占位，便于前端直接取用）
        out = [
            {"kind": "out", "cnt": 0, "total": 0.0, "tax": 0.0},
            {"kind": "in", "cnt": 0, "total": 0.0, "tax": 0.0},
        ]
        for r in conn.execute(
                "SELECT kind, COUNT(*) AS cnt, COALESCE(SUM(amount),0) AS total, "
                "COALESCE(SUM(tax_amount),0) AS tax FROM invoices"
                + agg_where + " GROUP BY kind",
                params).fetchall():
            for row in out:
                if row["kind"] == r["kind"]:
                    row["cnt"] = r["cnt"]
                    row["total"] = float(r["total"] or 0)
                    row["tax"] = float(r["tax"] or 0)
    invoices = [dict(r) for r in rows]
    summary = _invoice_summary_text(out)
    return {
        "invoices": invoices,
        "by_kind": out,
        "flags": [],
        "summary": summary,
    }


def _invoice_summary_text(by_kind) -> str:
    """按 kind 汇总成大白话（含 0 值占位，便于前端展示）"""
    def _get(kind):
        for b in by_kind:
            if b["kind"] == kind:
                return {"kind": kind, "c": int(b.get("cnt") or 0),
                        "total": float(b.get("total") or 0),
                        "tax": float(b.get("tax") or 0)}
        return {"kind": kind, "c": 0, "total": 0.0, "tax": 0.0}

    out_, in_ = _get("out"), _get("in")
    text = f"销项开了 {out_['c']} 张、共 {out_['total']:,.0f} 元"
    if in_["c"]:
        text += f"；进项收了 {in_['c']} 张、共 {in_['total']:,.0f} 元"
    else:
        text += "；还没有进项票，进货时记得要票，能抵扣"
    return text + "。"