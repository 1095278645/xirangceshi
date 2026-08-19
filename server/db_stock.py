"""db_stock.py — SQLite 数据层 · 库存进销存

对标 Frappe Books 的进销存 / POS：把「进货只记金额」升级为「按商品记数量、成本、库存」。
掌柜口径（亲民）：店主只关心「货还剩多少 / 哪些快卖没了要补 / 哪些快过期了别烂在手里」。
纯本地算法，不依赖 AI。
  - products：商品/原材料档案（库存、安全库存、单位成本、保质期）
  - stock_movements：入库/出库/盘点流水，自动更新库存
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

__all__ = [
    "add_product", "list_products", "update_product", "delete_product",
    "move_stock", "stock_summary",
]


def _conn():
    from db import get_conn
    return get_conn()


# ---------------- 商品 / 原材料档案 ----------------
def add_product(name: str, category: str = "", unit: str = "", stock_qty: float = 0,
                safety_stock: float = 0, unit_cost: float = 0,
                expiry_date: str = "", supplier: str = "", note: str = ""):
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO products(name, category, unit, stock_qty, safety_stock, "
            "unit_cost, expiry_date, supplier, note) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (name, category, unit, stock_qty, safety_stock, unit_cost,
             expiry_date, supplier, note))
        return cur.lastrowid


def list_products():
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def update_product(pid: int, **fields):
    """按白名单更新商品字段；返回是否更新成功"""
    allowed = {"name", "category", "unit", "safety_stock", "unit_cost",
               "expiry_date", "supplier", "note"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return False
    params.append(pid)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE products SET {','.join(sets)} WHERE id=?", params)
        return cur.rowcount > 0


def delete_product(pid: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM products WHERE id=?", (pid,))
        return cur.rowcount > 0


# ---------------- 库存变动（入库 / 出库 / 盘点） ----------------
def move_stock(pid: int, movement_type: str, qty: float, note: str = ""):
    """入库(in)/出库(out)/盘点(adj) 并自动更新库存；返回最新库存量"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        qty = float(qty)
        cur_qty = float(row["stock_qty"] or 0)
        if movement_type == "in":
            new_qty = cur_qty + qty
        elif movement_type == "out":
            new_qty = max(cur_qty - qty, 0)  # 出库不超库存
        elif movement_type == "adj":
            new_qty = max(qty, 0)            # 盘点：直接设为盘点数
        else:
            return None
        conn.execute("UPDATE products SET stock_qty=? WHERE id=?", (new_qty, pid))
        conn.execute(
            "INSERT INTO stock_movements(product_id, movement_type, qty, note) "
            "VALUES(?,?,?,?)",
            (pid, movement_type, qty, note))
    d = dict(row)
    d["stock_qty"] = new_qty
    return d


def _low_stock_rows(conn):
    """低库存 + 快过期预警清单（internal）"""
    today = date.today()
    warn = today + timedelta(days=7)
    low = [dict(r) for r in conn.execute(
        "SELECT * FROM products WHERE stock_qty <= safety_stock AND safety_stock > 0 "
        "ORDER BY id").fetchall()]
    expiring = []
    for r in conn.execute("SELECT * FROM products").fetchall():
        d = dict(r)
        exp = (d.get("expiry_date") or "")[:10]
        if not exp or not d.get("stock_qty") or float(d["stock_qty"]) <= 0:
            continue
        try:
            exp_dt = datetime.strptime(exp, "%Y-%m-%d").date()
        except ValueError:
            continue
        if exp_dt <= warn:
            d["expiring_days"] = (exp_dt - today).days
            expiring.append(d)
    return low, expiring


def stock_summary():
    """库存总览（亲民：货值 / 快补货 / 快过期）。返回商品清单 + 大白话预警"""
    with _conn() as conn:
        products = [dict(r) for r in conn.execute(
            "SELECT * FROM products ORDER BY id DESC").fetchall()]
        low, expiring = _low_stock_rows(conn)
    total_value = sum(float(p["stock_qty"] or 0) * float(p["unit_cost"] or 0)
                      for p in products)
    total_items = sum(1 for p in products if p["stock_qty"] > 0)
    flags = []
    for p in low:
        flags.append(f"{p['name']} 只剩 {p['stock_qty']:,.0f}{p['unit']}，"
                     f"低于补货线 {p['safety_stock']:,.0f}，该补货了。")
    for p in expiring:
        days = p.get("expiring_days", 0)
        flags.append(f"{p['name']} 还有 {p['stock_qty']:,.0f}{p['unit']}，"
                     f"{'已经过期' if days < 0 else f'{days} 天内快过期'}，别压货。")
    summary = f"店里有 {total_items} 种货在库存着，货值约 {total_value:,.0f} 元。"
    if flags:
        summary += " " + "；".join(flags[:2])
    return {
        "products": products,
        "total_items": total_items,
        "total_value": round(total_value, 2),
        "low_stock": low,
        "expiring": expiring,
        "flags": flags,
        "summary": summary,
    }