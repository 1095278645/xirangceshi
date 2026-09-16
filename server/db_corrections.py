"""db_corrections.py — 交易更正：编辑 / 作废 / 退货冲销（含审计留痕）

为什么必须有：原先交易一旦入账就**永远无法修改或删除**，凭证表虽有 status
字段却没有作废实现。叠加 AI 会分类错误（实测「交增值税 1000」被判成「办公费」），
后果是错的账永远留在那里、店主无从察觉；退货/退款也压根无法表达。

三种更正语义（都要留痕，不能"悄悄消失"）：
  - **编辑**：改金额/分类/事由/顾客等。旧凭证作废并生成作废分录，
    再生成一张新凭证 —— 不是就地改写，保证凭证号连续可查。
  - **作废**：这笔不该存在（录错、重复记）。标记 voided 并冲销凭证，
    记录仍保留可查，但**不计入任何合计**。
  - **退货冲销**：买方退货 / 退款。生成一条金额为**负**的关联记录，
    与原记录相加自然抵消；原记录标记 refunded。这样月汇总无需特殊处理，
    负数会自动从收入里扣掉。

所有操作写 transaction_audits（动作 / 前后快照 / 原因 / 时间）。
"""
from __future__ import annotations

import json
import logging
from datetime import date

from categories import ACCOUNT_NAMES, CATEGORY_TO_ACCOUNTS, FRIENDLY_NAMES

log = logging.getLogger("db_corrections")

__all__ = [
    "edit_transaction", "void_transaction", "refund_transaction",
    "list_transaction_audits", "get_transaction",
]

# 允许编辑的字段（白名单，避免调用方顺手改 created_at / source 等）
EDITABLE_FIELDS = frozenset({"item", "amount", "category", "counterparty", "note",
                             "trans_type", "customer_id"})


def _conn():
    from db import get_conn      # 惰性导入，避免与聚合层循环依赖
    return get_conn()


def _row_to_dict(row) -> dict | None:
    return dict(row) if row else None


def _audit(conn, txn_id: int, action: str, before: dict | None,
           after: dict | None, reason: str = "") -> None:
    conn.execute(
        "INSERT INTO transaction_audits(transaction_id, action, before_json, "
        "after_json, reason) VALUES(?,?,?,?,?)",
        (txn_id, action,
         json.dumps(before, ensure_ascii=False, default=str) if before else "",
         json.dumps(after, ensure_ascii=False, default=str) if after else "",
         reason))


def _make_voucher(conn, txn_id: int, amount: float, trans_type: str,
                  category: str, summary: str, reversal: bool = False) -> dict:
    """为交易生成借贷凭证；reversal=True 时借贷方向对调（用于冲销）。

    单独实现而不复用 _auto_voucher：更正场景的凭证摘要要写成
    「作废/退货冲销」，而且要能指定方向，和首次记账的语义不同。

    注意 amount 一律取**绝对值**：会计凭证的金额是正的数量级，方向由借贷
    表示。若把退货的负数金额写进分录，月度合计（按 transactions.amount 求和）
    会与凭证（按分录求和）口径不一致，抵消会翻倍 —— 实测全额退货后
    收入变成 -50 而不是 50。
    """
    mapping = CATEGORY_TO_ACCOUNTS.get(category)
    if not mapping:
        log.warning("分类 %r 无科目映射，凭证兜底", category)
        mapping = (CATEGORY_TO_ACCOUNTS["主营业务收入"] if trans_type == "income"
                   else CATEGORY_TO_ACCOUNTS["办公费"])
    # CATEGORY_TO_ACCOUNTS 的约定是 (借方科目, 贷方科目)
    dr_code, cr_code = mapping[0], mapping[1]
    if reversal:
        dr_code, cr_code = cr_code, dr_code

    today = date.today().isoformat()
    base = f"记-{today[:7].replace('-', '')}-"
    seq = conn.execute(
        "SELECT COALESCE(MAX(CAST(REPLACE(voucher_no, ?, '') AS INTEGER)), 0) "
        "FROM vouchers WHERE voucher_no LIKE ?", (base, base + "%")
    ).fetchone()[0] + 1
    vid = None
    voucher_no = None
    for _ in range(1000):
        voucher_no = f"{base}{seq:04d}"
        try:
            cur = conn.execute(
                "INSERT INTO vouchers(voucher_no, voucher_date, summary, transaction_id) "
                "VALUES(?,?,?,?)", (voucher_no, today, summary, txn_id))
            vid = cur.lastrowid
            break
        except Exception:  # sqlite3.IntegrityError：撞号则递增
            seq += 1
    if vid is None:
        raise RuntimeError("生成更正凭证失败")

    magnitude = abs(amount)
    for code, direction in ((dr_code, "debit"), (cr_code, "credit")):
        conn.execute(
            "INSERT INTO voucher_entries(voucher_id, account_code, account_name, "
            "direction, amount) VALUES(?,?,?,?,?)",
            (vid, code, ACCOUNT_NAMES.get(code, code), direction, magnitude))
    return {"voucher_no": voucher_no, "reversal": reversal,
            "debit": ACCOUNT_NAMES.get(dr_code, dr_code),
            "credit": ACCOUNT_NAMES.get(cr_code, cr_code)}


def _void_vouchers(conn, txn_id: int, reason: str) -> list[dict]:
    """把该交易的凭证标记为作废，并生成对调方向的作废分录。"""
    rows = conn.execute(
        "SELECT id, voucher_no FROM vouchers WHERE transaction_id=?", (txn_id,)).fetchall()
    out = []
    for v in rows:
        entries = conn.execute(
            "SELECT account_code, account_name, direction, amount FROM voucher_entries "
            "WHERE voucher_id=? ORDER BY id", (v["id"],)).fetchall()
        for e in entries:
            flip = "credit" if e["direction"] == "debit" else "debit"
            conn.execute(
                "INSERT INTO voucher_entries(voucher_id, account_code, account_name, "
                "direction, amount) VALUES(?,?,?,?,?)",
                (v["id"], e["account_code"], e["account_name"], flip, e["amount"]))
        conn.execute("UPDATE vouchers SET status='void' WHERE id=?", (v["id"],))
        out.append({"voucher_no": v["voucher_no"], "reversed": True, "reason": reason})
    return out


def get_transaction(txn_id: int) -> dict | None:
    with _conn() as conn:
        return _row_to_dict(conn.execute(
            "SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone())


def _require_active(conn, txn_id: int, op: str) -> dict:
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if not row:
        raise ValueError(f"交易不存在：{txn_id}")
    t = dict(row)
    status = t.get("status") or "active"
    if status != "active":
        raise ValueError(
            f"该交易已是「{status}」状态，不能再次{op}（如需修正请直接改状态相关记录）")
    return t


# ---------------- 编辑 ----------------

def edit_transaction(txn_id: int, *, reason: str = "", **fields) -> dict:
    """更正一笔交易。只允许改 EDITABLE_FIELDS 里的字段。

    实现方式：旧凭证作废（生成对调分录）→ 更新交易 → 生成新凭证。
    不做就地改写凭证，保证凭证号连续、审计链完整。
    """
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"不支持修改这些字段：{sorted(unknown)}")
    if not fields:
        raise ValueError("没有要修改的内容")

    with _conn() as conn:
        before = _require_active(conn, txn_id, "编辑")
        merged = {**before, **fields}
        # 分类必须有效，否则会落到兜底科目（见 db_ledger 的告警）
        if merged.get("category") not in CATEGORY_TO_ACCOUNTS:
            raise ValueError(f"分类无效：{merged.get('category')!r}")
        if merged.get("trans_type") not in ("income", "expense"):
            raise ValueError("trans_type 只能是 income / expense")
        try:
            amount = float(merged.get("amount") or 0)
        except (TypeError, ValueError):
            raise ValueError("金额必须是数字")
        if amount < 0:
            raise ValueError("金额不能为负（退货请用退货冲销）")

        voided = _void_vouchers(conn, txn_id, reason or "编辑更正")
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE transactions SET {sets} WHERE id=?",
                     (*fields.values(), txn_id))
        voucher = _make_voucher(
            conn, txn_id, amount, merged["trans_type"], merged["category"],
            f"[更正] {merged.get('item') or ''}", reversal=False)

        after = _row_to_dict(conn.execute(
            "SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone())
        _audit(conn, txn_id, "edit", before, after, reason)
    log.info("交易 %s 已更正：%s", txn_id, sorted(fields))
    return {"ok": True, "transaction": after, "new_voucher": voucher,
            "voided_vouchers": voided}


# ---------------- 作废 ----------------

def void_transaction(txn_id: int, reason: str = "") -> dict:
    """作废一笔交易：标记 voided + 冲销凭证，记录保留可查但不计入合计。"""
    with _conn() as conn:
        before = _require_active(conn, txn_id, "作废")
        voided = _void_vouchers(conn, txn_id, reason or "作废")
        conn.execute(
            "UPDATE transactions SET status='voided', voided_reason=? WHERE id=?",
            (reason, txn_id))
        after = _row_to_dict(conn.execute(
            "SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone())
        _audit(conn, txn_id, "void", before, after, reason)
    log.info("交易 %s 已作废：%s", txn_id, reason)
    return {"ok": True, "transaction": after, "voided_vouchers": voided}


# ---------------- 退货冲销 ----------------

def refund_transaction(txn_id: int, amount: float | None = None,
                       reason: str = "", customer_id: int | None = None) -> dict:
    """买方退货 / 退款：生成金额为负的关联记录，与原记录相加自然抵消。

    amount 省略时全额退；也支持部分退（金额小于原额）。
    """
    with _conn() as conn:
        before = _require_active(conn, txn_id, "退货")
        if (before.get("trans_type") or "income") != "income":
            raise ValueError("退货冲销只适用于收入类交易；支出类请用「编辑」或「作废」")
        orig_amount = float(before.get("amount") or 0)
        if orig_amount <= 0:
            raise ValueError("原交易金额为 0，无需退货")
        refund_amt = orig_amount if amount is None else float(amount)
        if refund_amt <= 0:
            raise ValueError("退货金额必须大于 0")
        if refund_amt > orig_amount:
            raise ValueError(f"退货金额 {refund_amt} 超过原交易金额 {orig_amount}")

        cid = customer_id if customer_id is not None else before.get("customer_id")
        cur = conn.execute(
            "INSERT INTO transactions(customer_id, trans_type, category, item, amount, "
            "counterparty, note, source, status, parent_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, "income", before.get("category") or "主营业务收入",
             before.get("item") or "", -refund_amt,
             before.get("counterparty") or "",
             f"[退货] {reason}" if reason else "[退货]",
             "manual", "refund", txn_id))
        refund_id = cur.lastrowid
        voucher = _make_voucher(
            conn, refund_id, refund_amt, "income",
            before.get("category") or "主营业务收入",
            f"[退货冲销] {before.get('item') or ''}", reversal=True)

        # 合计口径：原记录保持 active（金额被负数记录抵消），**不能**改成
        # refunded —— refunded 会被 _ACTIVE_FILTER 排除，而冲销记录又计了一次
        # 负数，等于抵消两遍（实测全额退货后收入变成 -100）。
        # 原记录是否"已退货"由 audit 与关联的 refund 记录体现，审计链完整。
        after = _row_to_dict(conn.execute(
            "SELECT * FROM transactions WHERE id=?", (refund_id,)).fetchone())
        _audit(conn, txn_id, "refund", before, after,
               f"{reason} 金额={refund_amt}")
    log.info("交易 %s 退货冲销 %.2f 元（新记录 %s）", txn_id, refund_amt, refund_id)
    return {"ok": True, "refund_id": refund_id, "refund_transaction": after,
            "voucher": voucher, "amount": -refund_amt,
            "fully_refunded": refund_amt >= orig_amount}


# ---------------- 审计查询 ----------------

def list_transaction_audits(txn_id: int | None = None, limit: int = 100) -> list[dict]:
    """更正历史。不传 txn_id 则返回最近的更动记录。"""
    with _conn() as conn:
        if txn_id:
            rows = conn.execute(
                "SELECT * FROM transaction_audits WHERE transaction_id=? "
                "ORDER BY id DESC LIMIT ?", (txn_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM transaction_audits ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
    return [dict(r) for r in rows]
