"""accounting.py — 会计闭环：科目余额表 / 利润表 / 资产负债表 / 期末结转

## 为什么需要

项目已有复式凭证（借贷平衡、66 科目），但没有**期末结转**与**财务报表**，
所以只是"记账 + 算税"，代账会计拿去接不上。这个模块补齐会计循环：

    记账 → 科目余额表 → 利润表 / 资产负债表 → 期末结转（损益 → 本年利润）

## 口径说明（重要）

- **余额**从凭证分录聚合：`净额 = 借方合计 − 贷方合计`；资产/费用类借增，
  负债/权益/收入类贷增，所以按科目类别取符号即为该科目的"正常余额"。
- **期初余额**：本项目是自建的流水账，没有更早的期初数据，因此
  「期初」= 所选期间之前的全部凭证累计。用户也可以显式录入期初余额
  （见 opening_balances），用于把历史账套接进来。
- **借贷平衡自检**：所有凭证的借方合计必须等于贷方合计。余额表里
  `sum(净额) == 0` 就是这个约束的体现 —— 不平说明凭证有问题，报表不可信，
  所以余额表会显式返回 `balanced` 标记。

## 期末结转的幂等设计

结转把损益类（收入/费用）余额转入「本年利润」，再转「利润分配」。
重复结转会把利润算两遍，所以：
  - `period_closings` 记录每个期间是否已结转；
  - 重新结转时**先冲销上一次的结转凭证**（红冲），再按当前数据重新生成 ——
    这样更正过流水之后重跑结转不会算错。
  - 结转期间不允许早于已结转的期间（避免倒挂）。
"""
from __future__ import annotations

import logging
from datetime import date

import config
from categories import ACCOUNT_CATEGORY_NAMES, ACCOUNT_NAMES, ACCOUNT_TITLES

log = logging.getLogger("accounting")

__all__ = [
    "trial_balance", "income_statement", "balance_sheet",
    "close_period", "list_closings", "reopen_period",
    "set_opening_balance", "list_opening_balances",
]

# 科目类别 → 是否损益类（期末要结转到本年利润）
PL_CATEGORIES = ("income", "expense")
# 正常余额方向：借增为 +1，贷增为 -1
_BY_CODE = {t[0]: {"name": t[1], "category": t[2], "direction": t[3], "level": t[4]}
            for t in ACCOUNT_TITLES}

# 利润表项目分组（按科目编码前缀归类，便于老板看懂）
REVENUE_GROUPS = {"5001": "主营业务收入", "5051": "其他业务收入", "5301": "营业外收入"}
EXPENSE_GROUPS = {
    "5401": "主营业务成本", "5402": "其他业务成本",
    "5601": "管理费用", "5602": "财务费用", "5603": "营业外支出", "5801": "所得税费用",
}


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

def set_opening_balance(account_code: str, amount: float, note: str = "") -> dict:
    """设置某科目的期初余额（正数=正常余额方向）。

    没有历史账套时不填也行 —— 此时"期初"就是所选期间之前的凭证累计。
    """
    if account_code not in _BY_CODE:
        raise ValueError(f"科目不存在：{account_code}")
    amt = float(amount or 0)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO opening_balances(account_code, amount, note) VALUES(?,?,?) "
            "ON CONFLICT(account_code) DO UPDATE SET amount=excluded.amount, "
            "note=excluded.note", (account_code, amt, note))
    return {"account_code": account_code, "amount": amt,
            "account_name": _BY_CODE[account_code]["name"]}


def list_opening_balances() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM opening_balances ORDER BY account_code").fetchall()
    return [dict(r) for r in rows]


def _opening_map(conn) -> dict[str, float]:
    try:
        rows = conn.execute("SELECT account_code, amount FROM opening_balances").fetchall()
        return {r["account_code"]: float(r["amount"] or 0) for r in rows}
    except Exception:  # noqa: BLE001  老库还没建表
        return {}


# ---------------- 科目余额表 ----------------

def trial_balance(period: str | None = None, opening: bool = True,
                  exclude_closing_vouchers: bool = False) -> dict:
    """科目余额表：每个科目的借方合计 / 贷方合计 / 期末净额。

    period='YYYY-MM' 时，期初=该月之前的累计，期末=期初+本月发生；
    period=None 时按全部凭证计算（期初为 0 或用户录入的期初余额）。

    exclude_closing_vouchers=True 时**不统计期末结转凭证**。结转流程必须用它：
    结转凭证本身已经把损益科目清零了，若算进去，重算时就"看不到"原始损益
    发生额，二次结转会把利润算成 0（实测踩到过）。
    """
    start, end = _period_bounds(period)
    # 期末结转凭证：这些凭证是结转动作产生的，重算损益时要排除。
    # 必须**同时**作用于当期发生额与期初累计 —— 只排当期的话，期初里仍含
    # 上月的结转分录，本月会把上月损益再结转一遍（实测多算了 500）。
    _excl = (" AND v.id NOT IN (SELECT voucher_id FROM period_closings "
             "WHERE voucher_id IS NOT NULL)") if exclude_closing_vouchers else ""

    with _conn() as conn:
        rows = conn.execute(
            "SELECT e.account_code, e.direction, COALESCE(SUM(e.amount),0) AS amt "
            "FROM voucher_entries e JOIN vouchers v ON v.id=e.voucher_id "
            "WHERE date(v.voucher_date) >= ? AND date(v.voucher_date) < ?" + _excl +
            " GROUP BY e.account_code, e.direction",
            (start + "-01", end + "-01")).fetchall()
        prior_rows = conn.execute(
            "SELECT e.account_code, e.direction, COALESCE(SUM(e.amount),0) AS amt "
            "FROM voucher_entries e JOIN vouchers v ON v.id=e.voucher_id "
            "WHERE date(v.voucher_date) < ?" + _excl +
            " GROUP BY e.account_code, e.direction",
            (start + "-01",)).fetchall() if period else []
        opens = _opening_map(conn) if opening else {}

    def _fold(rs) -> dict[str, dict[str, float]]:
        acc: dict[str, dict[str, float]] = {}
        for r in rs:
            d = acc.setdefault(r["account_code"], {"debit": 0.0, "credit": 0.0})
            d[r["direction"]] += float(r["amt"] or 0)
        return acc

    cur = _fold(rows)
    prior = _fold(prior_rows)

    lines = []
    total_debit = total_credit = 0.0
    for code, info in _BY_CODE.items():
        c = cur.get(code, {"debit": 0.0, "credit": 0.0})
        p = prior.get(code, {"debit": 0.0, "credit": 0.0})
        open_net = 0.0
        if opening:
            # 期初净额：按科目类别表示"正常余额方向"
            # 资产/费用类借增 → 借-贷 本身就是正常方向
            # 负债/权益/收入类贷增 → 取负号才是正常方向（"收入期初 100"才读得通）
            debit_minus_credit = p["debit"] - p["credit"]
            open_net = debit_minus_credit if info["direction"] > 0 else -debit_minus_credit
            open_net += opens.get(code, 0.0)
        period_net = c["debit"] - c["credit"]
        # opening=False 时 close_net 就只是本期发生额（结转流程要的正是这个）
        close_net = (open_net if info["direction"] > 0 else -open_net) + period_net
        if c["debit"] == 0 and c["credit"] == 0 and open_net == 0:
            continue            # 完全没发生也没余额的科目不占版面
        total_debit += c["debit"]
        total_credit += c["credit"]
        lines.append({
            "account_code": code,
            "account_name": info["name"],
            "category": info["category"],
            "category_name": ACCOUNT_CATEGORY_NAMES.get(info["category"], info["category"]),
            "opening": round(open_net, 2),
            "debit": round(c["debit"], 2),
            "credit": round(c["credit"], 2),
            "closing": round(close_net, 2),
        })

    # 借贷平衡自检：所有科目净额之和应为 0（复式记账的必然结果）
    #
    # 但**期初余额**是个例外：它按科目逐个录入，只录了资产（现金/存款）而没录
    # 对应来源时，净额之和会是"期初净额"而非 0。资产负债表对这种"业主投入"
    # 会显式补一行权益，所以那里是平的；余额表若还照搬 net_sum == 0，
    # 同一份数据会出现"资产负债表平衡、余额表不平衡"的自相矛盾（实测：
    # 录入期初银行存款 20000 后，资产负债表平衡而余额表报不平衡）。
    # 因此这里把两种口径分开：balanced 只管**凭证本身**是否平衡，
    # 期初带来的差额单独用 opening_gap 说明。
    opening_gap = round(sum(
        float(ob.get("amount") or 0) *
        (1 if (_BY_CODE.get(ob["account_code"]) or {}).get("direction", 1) > 0 else -1)
        for ob in (list_opening_balances() if opening else [])))
    net_sum = round(sum(x["closing"] for x in lines) - opening_gap, 2)
    balanced = abs(total_debit - total_credit) < 0.01 and abs(net_sum) < 0.01
    return {
        "period": period or "全部",
        "lines": lines,
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "balanced": balanced,
        "net_sum": net_sum,
        "opening_gap": opening_gap,
        "note": ("借贷平衡" if balanced else "凭证借贷不平，请检查凭证")
                + (f"；期初余额净额 {opening_gap:,.2f} 元来自业主投入（已在资产负债表"
                   f"按「实收资本（期初投入）」列示）" if abs(opening_gap) > 0.01 else ""),
    }


# ---------------- 利润表 ----------------

def income_statement(period: str | None = None) -> dict:
    """利润表：收入 − 成本费用 = 净利润（按科目编码前缀归类）。

    两个口径必须同时满足，否则报表会自相矛盾：

      1. **只看本期发生额**，不能看期末余额。余额表的期末余额 = 期初 + 本期发生，
         而期初包含该期间之前的全部损益发生额。早期版本直接取 closing，于是
         "选一个完全没有流水的期间"也报出利润 —— 实测 2099-01（空期间）报出
         净利 700 元，因为期初累计被当成了本期发生。用 opening=False 修掉。

      2. **要包含结转凭证**。结转会把损益科目清零，所以结转后本期净利应显示 0；
         若排除结转凭证，结转后仍显示原利润（实测 700），与"已结转"自相矛盾。
         —— 需要"原始损益发生额"的是**结转流程本身**（它要算该转多少），
         那个场景走 trial_balance(exclude_closing_vouchers=True)，不走这里。
    """
    tb = trial_balance(period, opening=False)
    by_code = {x["account_code"]: x for x in tb["lines"]}

    def _sum_group(groups: dict, kind: str) -> dict:
        """按科目编码前缀归组并转成"好读的正数"。

        本期发生额里：收入类净额是**负数**（贷方），费用类是**正数**（借方）。
        报表要显示成人们习惯的正数，所以：
          - 收入：取 `-本期净额`
          - 费用：取 `+本期净额`
        （早期版本对费用也取反，导致利润表里收入/费用/净利全是负的。）
        """
        out = {}
        for code, label in groups.items():
            amt = sum(x["closing"] for c, x in by_code.items()
                      if c == code or c.startswith(code))
            amt = -amt if kind == "income" else amt
            if abs(amt) > 0.001:
                out[label] = round(amt, 2)
        return out

    revenue = _sum_group(REVENUE_GROUPS, "income")
    expense = _sum_group(EXPENSE_GROUPS, "expense")
    total_revenue = round(sum(revenue.values()), 2)
    total_expense = round(sum(expense.values()), 2)
    net = round(total_revenue - total_expense, 2)

    return {
        "period": period or "全部",
        "revenue": revenue,
        "expense": expense,
        "total_revenue": total_revenue,
        "total_expense": total_expense,
        "net_profit": net,
        "note": f"收入 {total_revenue:,.0f} − 成本费用 {total_expense:,.0f} "
                f"= 净利 {net:,.0f}",
    }


# ---------------- 资产负债表 ----------------

def balance_sheet(as_of: str | None = None) -> dict:
    """资产负债表（简化版）：资产 = 负债 + 所有者权益（含未结转的当期利润）。

    as_of='YYYY-MM-DD' 只取该日之前的凭证；None 取全部。
    """
    cutoff = (as_of or "9999-12-31") + " 23:59:59"
    with _conn() as conn:
        rows = conn.execute(
            "SELECT e.account_code, e.direction, COALESCE(SUM(e.amount),0) AS amt "
            "FROM voucher_entries e JOIN vouchers v ON v.id=e.voucher_id "
            "WHERE datetime(v.voucher_date) <= datetime(?) "
            "GROUP BY e.account_code, e.direction", (cutoff,)).fetchall()
        opens = _opening_map(conn)

    acc: dict[str, dict[str, float]] = {}
    for r in rows:
        d = acc.setdefault(r["account_code"], {"debit": 0.0, "credit": 0.0})
        d[r["direction"]] += float(r["amt"] or 0)

    def _rows_for(category: str) -> list[dict]:
        out = []
        for code, info in _BY_CODE.items():
            if info["category"] != category:
                continue
            c = acc.get(code, {"debit": 0.0, "credit": 0.0})
            net = c["debit"] - c["credit"]
            # 取正常余额方向
            normal = net if info["direction"] > 0 else -net
            normal += opens.get(code, 0.0)
            if abs(normal) > 0.001:
                out.append({"account_code": code, "account_name": info["name"],
                            "amount": round(normal, 2)})
        return out

    assets = _rows_for("asset")
    liabilities = _rows_for("liability")
    equity = _rows_for("equity")
    # 未结转的当期损益也要算进权益，否则等式不平。
    # 收入类期末净额为负（贷方），费用类为正（借方），所以
    # 当期利润 = -Σ收入净额 - Σ费用净额
    tb = trial_balance(None)
    pl_profit = round(
        -sum(x["closing"] for x in tb["lines"] if x["category"] == "income")
        - sum(x["closing"] for x in tb["lines"] if x["category"] == "expense"), 2)

    # 期初权益调整：期初余额是按「正常余额方向」录入的。若只录了期初资产
    # （现金/存款）而没有对应的权益分录，产权一侧就缺一块，会计等式不成立。
    # 会计上的处理是计入权益（相当于业主投入的期初资本），这里显式列出来，
    # 让使用者一眼看到"这块是期初补进来的"，而不是让报表悄悄不平。
    opening_rows = list_opening_balances()
    opening_debit = sum(
        float(ob.get("amount") or 0) for ob in opening_rows
        if (_BY_CODE.get(ob["account_code"]) or {}).get("direction", 1) > 0)
    opening_credit = sum(
        float(ob.get("amount") or 0) for ob in opening_rows
        if (_BY_CODE.get(ob["account_code"]) or {}).get("direction", 1) < 0)
    opening_equity = round(opening_debit - opening_credit, 2)
    if abs(opening_equity) > 0.001:
        # 挂在「实收资本」名下更贴近事实（业主投入），但用独立名称避免混淆
        equity = equity + [{"account_code": "3001", "account_name": "实收资本（期初投入）",
                            "amount": opening_equity, "opening_adjustment": True}]

    total_assets = round(sum(x["amount"] for x in assets), 2)
    total_liabilities = round(sum(x["amount"] for x in liabilities), 2)
    # 注意：opening_equity 已经作为一行加进 equity 列表了，这里**不能再加一次**
    # （早先重复计入过，产权侧变成 20040 而不是 10040）
    total_equity = round(sum(x["amount"] for x in equity) + pl_profit, 2)

    same = abs(total_assets) < 0.01 and abs(total_liabilities + total_equity) < 0.01
    return {
        "as_of": as_of or "最新",
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "opening_equity": opening_equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "unclosed_profit": pl_profit,
        "liabilities_and_equity": round(total_liabilities + total_equity, 2),
        "balanced": abs(total_assets - (total_liabilities + total_equity)) < 0.01,
        "note": "资产 = 负债 + 所有者权益（含未结转损益与期初权益调整）",
    }


# ---------------- 期末结转 ----------------

# 本年利润 / 利润分配科目（在 categories.ACCOUNT_TITLES 里已存在）
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
