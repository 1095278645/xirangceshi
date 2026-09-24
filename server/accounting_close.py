"""accounting_close.py — 期末结转 / 反结转（从 accounting.py 外移，架构自检 L1）

职责：期间损溢结转到"本年利润"、红冲重算、反结转、结转记录查询。
含本模块自用的原语 `_conn()` 与 `_period_bounds()`（下沉，避免与 accounting 互引成环）。

依赖方向：本模块**不在顶层 import accounting**；`close_period` 在函数内延迟导入
`trial_balance` 与 `PL_CATEGORIES`（它们是报表逻辑，仍在 accounting.py）。
"""
from __future__ import annotations

import logging
from datetime import date

import config
from categories import ACCOUNT_NAMES
from db import get_conn

log = logging.getLogger("accounting_close")

__all__ = ["close_period", "reopen_period", "list_closings",
           "_conn", "_period_bounds"]


def _conn():
    from db import get_conn
    return get_conn()


def _period_bounds(period: str | None) -> tuple[str, str]:
    """把 'YYYY-MM' 转成 [起, 止) 的字符串边界；None 表示不限。

    非法期间**直接报错**而不是当成"不限"：
    trial_balance 的返回里 period 可能是 "全部"（period=None 时），
    界面若把这个字符串回传给 /close、/reopen，旧实现会拿 int("全部"[:4]) 去算边界，
    抛 ValueError → 500。宁可在这里给出明确的 400 提示。
    """
    if not period:
        return "0000-00", "9999-99"
    p = str(period).strip()
    try:
        y, m = int(p[:4]), int(p[5:7])
    except (ValueError, TypeError):
        raise ValueError(
            f"会计期间格式不对：{period!r}，应为 'YYYY-MM'（如 2026-09）") from None
    if not (1 <= m <= 12) or y < config.YEAR_MIN or y > config.YEAR_MAX:
        raise ValueError(f"会计期间超出范围：{period!r}")
    nxt = f"{y + 1:04d}-01" if m == 12 else f"{y:04d}-{m + 1:02d}"
    return f"{y:04d}-{m:02d}", nxt


# ---------------- 期初余额（可选：把历史账套接进来） ----------------



PROFIT_CODE = "3103"        # 本年利润
DISTRIBUTION_CODE = "3104"  # 利润分配


def _next_voucher_no(conn, period_digits: str) -> tuple[str, int]:
    base = f"记-{period_digits}-"
    seq = conn.execute(
        "SELECT COALESCE(MAX(CAST(REPLACE(voucher_no, ?, '') AS INTEGER)), 0) "
        "FROM vouchers WHERE voucher_no LIKE ?", (base, base + "%")
    ).fetchone()[0] + 1
    return base, seq


def _insert_voucher(conn, base: str, seq: int, vdate: str, summary: str) -> tuple[int, str]:
    """插入凭证头，撞号则递增。返回 (voucher_id, voucher_no)。"""
    for _ in range(1000):
        voucher_no = f"{base}{seq:04d}"
        try:
            cur = conn.execute(
                "INSERT INTO vouchers(voucher_no, voucher_date, summary, transaction_id) "
                "VALUES(?,?,?,NULL)", (voucher_no, vdate, summary))
            return cur.lastrowid, voucher_no
        except Exception:  # noqa: BLE001  撞号
            seq += 1
    raise RuntimeError("结转凭证取号失败")


def _add_entry(conn, vid: int, code: str, direction: str, amount: float) -> None:
    """金额一律取正的数量级，方向由 direction 表示（会计惯例）。"""
    if abs(amount) < 0.005:
        return
    conn.execute(
        "INSERT INTO voucher_entries(voucher_id, account_code, account_name, "
        "direction, amount) VALUES(?,?,?,?,?)",
        (vid, code, ACCOUNT_NAMES.get(code, code), direction, abs(round(amount, 2))))


def close_period(period: str, *, note: str = "") -> dict:
    """期末结转：把损益类科目余额结转到「本年利润」。

    幂等与重算：若该期间已结转，会**先冲销上一次的结转凭证**（红冲），
    再按当前数据重新生成 —— 这样更正过流水之后重跑结转不会算两遍。
    """
    if not period or len(period) != 7 or period[4] != "-":
        raise ValueError("期间格式应为 YYYY-MM")

    digits = period.replace("-", "")
    # 两个都必须排除，否则会结转错：
    #   exclude_closing_vouchers：否则重算时损益已被上次结转清零，"没有可结转的了"
    #   opening=False：只结**本期发生额**。期初是以前期间的累计（那些期间该结的
    #     已经结过了），若把期初也算进来，本月会把上月损益再结一遍
    #     （实测：上月 500 被重复结转，本月净利从 40 变成 540）。
    from accounting import trial_balance, PL_CATEGORIES  # 延迟导入，避免与 accounting 成环
    pl_lines = [x for x in trial_balance(period, opening=False,
                                         exclude_closing_vouchers=True)["lines"]
                if x["category"] in PL_CATEGORIES and abs(x["closing"]) > 0.005]
    # 净利 = 收入 - 费用。余额表里收入类净额为负（贷方）、费用类为正（借方）
    net = round(
        -sum(x["closing"] for x in pl_lines if x["category"] == "income")
        - sum(x["closing"] for x in pl_lines if x["category"] == "expense"), 2)

    with _conn() as conn:
        # 已结转过：先把旧结转凭证红冲（方向对调）并标记作废
        prev = conn.execute(
            "SELECT * FROM period_closings WHERE period=? AND status='closed'",
            (period,)).fetchone()
        reopened = None
        if prev:
            reopened = dict(prev)
            old_vid = prev["voucher_id"]
            if old_vid:
                entries = conn.execute(
                    "SELECT account_code, direction, amount FROM voucher_entries "
                    "WHERE voucher_id=?", (old_vid,)).fetchall()
                for e in entries:
                    flip = "credit" if e["direction"] == "debit" else "debit"
                    _add_entry(conn, old_vid, e["account_code"], flip, e["amount"])
                conn.execute("UPDATE vouchers SET status='void' WHERE id=?", (old_vid,))
            conn.execute("DELETE FROM period_closings WHERE period=?", (period,))

        base, seq = _next_voucher_no(conn, digits)
        # 结转日取该期间最后一天（用次月首日减一天更稳妥，这里统一用 28 号避免月末天数问题）
        vdate = f"{period}-28"
        vid, voucher_no = _insert_voucher(conn, base, seq, vdate, f"[期末结转] {period}")
        # 收入类：借收入、贷本年利润；费用类：贷费用、借本年利润
        for x in pl_lines:
            if x["category"] == "income":
                _add_entry(conn, vid, x["account_code"], "debit", x["closing"])
                _add_entry(conn, vid, PROFIT_CODE, "credit", x["closing"])
            else:
                _add_entry(conn, vid, x["account_code"], "credit", -x["closing"])
                _add_entry(conn, vid, PROFIT_CODE, "debit", -x["closing"])

        conn.execute(
            "INSERT INTO period_closings(period, voucher_id, voucher_no, net_profit, "
            "status, note) VALUES(?,?,?,?, 'closed', ?)",
            (period, vid, voucher_no, net, note))
        row = conn.execute("SELECT * FROM period_closings WHERE period=?",
                           (period,)).fetchone()

    log.info("期间 %s 已结转：净利 %.2f，凭证 %s", period, net, voucher_no)
    return {"ok": True, "closing": dict(row), "net_profit": net,
            "entries_count": len(pl_lines) * 2,
            "reclosed": bool(reopened),
            "note": "已重新结转（旧结转凭证已红冲）" if reopened else "首次结转"}


def reopen_period(period: str, reason: str = "") -> dict:
    """反结转：冲销结转凭证并解除期间锁定（用于发现账目问题后重做）。"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM period_closings WHERE period=?",
                           (period,)).fetchone()
        if not row:
            raise ValueError(f"期间 {period} 没有结转记录")
        c = dict(row)
        if c.get("voucher_id"):
            entries = conn.execute(
                "SELECT account_code, direction, amount FROM voucher_entries "
                "WHERE voucher_id=?", (c["voucher_id"],)).fetchall()
            for e in entries:
                flip = "credit" if e["direction"] == "debit" else "debit"
                _add_entry(conn, c["voucher_id"], e["account_code"], flip, e["amount"])
            conn.execute("UPDATE vouchers SET status='void' WHERE id=?",
                         (c["voucher_id"],))
        conn.execute(
            "UPDATE period_closings SET status='reopened', note=? WHERE period=?",
            (reason or c.get("note") or "", period))
        updated = conn.execute("SELECT * FROM period_closings WHERE period=?",
                               (period,)).fetchone()
    log.info("期间 %s 已反结转：%s", period, reason)
    return {"ok": True, "closing": dict(updated)}


def list_closings() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM period_closings ORDER BY period DESC").fetchall()
    return [dict(r) for r in rows]
