"""db_collections.py — 收款请求（收款即入账）

**能力边界（必须说清楚）**：真正做聚合收款需要支付牌照与商户资质，本项目做不到。
这里实现的是"收款请求 → 顾客扫码确认 → 店主确认到账 → 自动入账"的闭环：

  1. 店主在记账页点「收款」，填金额（可选：事由、熟客）→ 生成一个收款请求
     和一个公开链接/二维码；
  2. 顾客扫码打开公开页，看到金额与店名，填个称呼点「已付款」；
  3. 店主看到"待确认"，点「确认到账」→ 自动写入一笔收入并生成借贷凭证；
  4. 同时给出播报文本，供到账语音播报。

这样替代的是原先"次日拉昨日微信账单"的滞后与遗漏；资金仍走店主自己的
收款码，系统只负责把"收了多少钱"可靠地变成账本记录（并顺带记下是谁付的，
天然把收款和熟客关联起来）。
"""
from __future__ import annotations

import logging
import secrets

log = logging.getLogger("db_collections")

__all__ = [
    "create_collection", "get_collection_by_token", "get_collection",
    "mark_collection_paid", "confirm_collection", "cancel_collection",
    "list_collections",
]

VALID_STATUS = ("pending", "paid", "confirmed", "cancelled")


def _conn():
    from db import get_conn      # 惰性导入，避免与聚合层循环依赖
    return get_conn()


def _row(row) -> dict | None:
    return dict(row) if row else None


def _new_token() -> str:
    """收款链接凭证：够长且不可猜（顾客凭它访问公开页，不能枚举别人的单子）。"""
    return secrets.token_urlsafe(16)


def create_collection(amount: float, item: str = "", customer_id: int | None = None,
                      note: str = "", payer_name: str = "") -> dict:
    """创建收款请求（pending）。返回含 token 的记录。"""
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        raise ValueError("金额必须是数字")
    if amt <= 0:
        raise ValueError("金额必须大于 0")
    if amt > 1_000_000:
        raise ValueError("单笔金额过大，请分次收款")

    token = _new_token()
    with _conn() as conn:
        # 极小概率撞 token，重试几次
        for _ in range(5):
            exists = conn.execute("SELECT 1 FROM payment_collections WHERE token=?",
                                  (token,)).fetchone()
            if not exists:
                break
            token = _new_token()
        cur = conn.execute(
            "INSERT INTO payment_collections(token, amount, item, customer_id, note, "
            "payer_name, status) VALUES(?,?,?,?,?,?,'pending')",
            (token, round(amt, 2), item, customer_id, note, payer_name))
        cid = cur.lastrowid
        row = conn.execute("SELECT * FROM payment_collections WHERE id=?", (cid,)).fetchone()
    log.info("收款请求已创建 id=%s 金额=%.2f", cid, amt)
    return _row(row)


def get_collection(cid: int) -> dict | None:
    with _conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM payment_collections WHERE id=?", (cid,)).fetchone())


def get_collection_by_token(token: str) -> dict | None:
    if not token:
        return None
    with _conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM payment_collections WHERE token=?", (token,)).fetchone())


def mark_collection_paid(token: str, payer_name: str = "") -> dict:
    """顾客侧：在公开页点「已付款」。只允许 pending → paid。"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM payment_collections WHERE token=?",
                          (token,)).fetchone()
        if not row:
            raise ValueError("收款请求不存在或已失效")
        c = dict(row)
        if c["status"] == "cancelled":
            raise ValueError("该收款请求已被取消")
        if c["status"] in ("paid", "confirmed"):
            # 重复点击不算错，直接返回当前状态（幂等）
            return c
        conn.execute(
            "UPDATE payment_collections SET status='paid', "
            "paid_at=datetime('now','localtime'), payer_name=? WHERE id=?",
            (payer_name or c.get("payer_name") or "", c["id"]))
        return _row(conn.execute("SELECT * FROM payment_collections WHERE id=?",
                                (c["id"],)).fetchone())


def confirm_collection(cid: int, *, item: str | None = None,
                       category: str = "主营业务收入") -> dict:
    """店主侧：确认到账 → 写入一笔收入并生成借贷凭证。

    只允许 pending/paid → confirmed，避免重复入账。
    """
    import db   # 写交易需要聚合层
    from categories import CATEGORY_TO_ACCOUNTS

    if category not in CATEGORY_TO_ACCOUNTS:
        raise ValueError(f"分类无效：{category!r}")

    with _conn() as conn:
        row = conn.execute("SELECT * FROM payment_collections WHERE id=?",
                          (cid,)).fetchone()
        if not row:
            raise ValueError("收款请求不存在")
        c = dict(row)
        if c["status"] == "confirmed":
            raise ValueError("该笔已确认入账，不能重复确认")
        if c["status"] == "cancelled":
            raise ValueError("该收款请求已被取消")
        final_item = (item or c.get("item") or "扫码收款").strip()

        # 顾客在公开页填的称呼可用于自动建熟客档案（收款顺手记住人）
        customer_id = c.get("customer_id")
        if not customer_id and c.get("payer_name"):
            cid_new, _is_new = db.find_or_create_customer(
                c["payer_name"], set_favorite=False)
            customer_id = cid_new

        txn_id, voucher = db.add_transaction(
            customer_id, final_item, c["amount"], "income", category,
            counterparty=c.get("payer_name") or "", note="[收款] 扫码收款")

        conn.execute(
            "UPDATE payment_collections SET status='confirmed', transaction_id=?, "
            "customer_id=?, confirmed_at=datetime('now','localtime') WHERE id=?",
            (txn_id, customer_id, cid))
        updated = _row(conn.execute("SELECT * FROM payment_collections WHERE id=?",
                                   (cid,)).fetchone())

    log.info("收款已确认入账 id=%s 交易=%s 金额=%.2f", cid, txn_id, c["amount"])
    return {"ok": True, "collection": updated, "transaction_id": txn_id,
            "voucher": voucher,
            "announce": f"收款成功，{c['amount']:.2f}元"}


def cancel_collection(cid: int, reason: str = "") -> dict:
    """取消收款请求（顾客没付、店主开错金额等）。已确认入账的不能取消。"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM payment_collections WHERE id=?",
                          (cid,)).fetchone()
        if not row:
            raise ValueError("收款请求不存在")
        c = dict(row)
        if c["status"] == "confirmed":
            raise ValueError("已确认入账的收款不能取消；如需更正请用交易更正功能")
        conn.execute(
            "UPDATE payment_collections SET status='cancelled', note=? WHERE id=?",
            (reason or c.get("note") or "", cid))
        return _row(conn.execute("SELECT * FROM payment_collections WHERE id=?",
                                (cid,)).fetchone())


def list_collections(status: str | None = None, limit: int = 50) -> list[dict]:
    """收款请求列表。status 可选：pending / paid / confirmed / cancelled。"""
    if status and status not in VALID_STATUS:
        raise ValueError(f"status 只能是 {VALID_STATUS}")
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM payment_collections WHERE status=? "
                "ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM payment_collections ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
    return [dict(r) for r in rows]
