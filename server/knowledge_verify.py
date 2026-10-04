"""knowledge_verify.py — 知识资产的运行期核验（真值对账）

从 knowledge_assets.py 搬出来（原文件超 400 行硬限），职责单一：
**把写下来的知识拿回真值上对一遍**，对不上就标记为"待复核"，不许继续当结论用。

  - `build_verifiers()`：{来源名: 零参可调用}，只调现有 db 层函数，不自己写 SQL；
  - `check_volatility()`：ok / drift / unknown 三分（**unknown 不等于 ok**）；
  - `verify_asset()`：批量或单条核验，默认写回 verified_at / verify_ok / drift_note。

不调 AI、不联网：核验必须在无 Key 环境也能跑（与 skill_cards / shop_snapshot 一致）。
模块级不 import knowledge_assets（避免循环导入），函数内惰性取用它的公开能力。
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

log = logging.getLogger("knowledge_verify")

__all__ = ["build_verifiers", "check_volatility", "verify_asset"]

# 证据里的 key=value（值支持千分位逗号与 %）与"无 key 的数字"
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([-\d.,]+)\s*%?")
_BARE_NUMBER_RE = re.compile(r"([-+]?\d[\d,]*(?:\.\d+)?)")


def _default_verifiers() -> dict:
    """默认真值来源。

    刻意经 `knowledge_assets` 命名空间取：这样
    `mock.patch("knowledge_assets.build_verifiers")` 也能拦住本模块的默认调用
    （测试与调用方只有一个注入点，不会出现"补了没生效"）。
    """
    import knowledge_assets as ka
    return ka.build_verifiers()


# ---------------- 运行期真值 ----------------
# 只调现有 db 层函数，不自己写 SQL 绕过数据层；任一来源取数失败 → 该 key 直接从
# 返回 dict 里剔除（**不塞 0**：0 是"数据就是 0"，取不到是"没数据"，两者不能混）。

def build_verifiers() -> dict:
    """{来源名: 零参可调用}：每个可调用返回该来源的**当前真值**。"""
    import db

    v: dict = {}

    # 今日 / 本月收支
    try:
        today = db.today_summary()
        month = db.monthly_summary()
    except Exception as e:  # noqa: BLE001 —— 取不到就少几个 key，不影响其它来源
        log.warning("收支真值读取失败（今日/本月相关来源跳过）：%s", e)
        today = month = None
    if today is not None:
        income = float(today.get("income") or 0)
        expense = float(today.get("expense") or 0)
        balance = float(today.get("balance") or 0)
        v["today_income"] = lambda value=income: value
        v["today_expense"] = lambda value=expense: value
        v["today_balance"] = lambda value=balance: value
    if month is not None:
        income = float(month.get("income") or 0)
        expense = float(month.get("expense") or 0)
        balance = float(month.get("balance") or 0)
        v["month_income"] = lambda value=income: value
        v["month_expense"] = lambda value=expense: value
        v["month_balance"] = lambda value=balance: value
        for cat in month.get("categories") or []:
            if cat.get("category") == "进货":
                purchase = float(cat.get("total") or 0)
                v["month_purchase"] = lambda value=purchase: value
                break

    # 本月进项票
    try:
        inv = db.invoice_summary(date.today().strftime("%Y-%m"))
        for item in inv.get("by_kind") or []:
            if item.get("kind") == "in":
                amount = float(item.get("total") or 0)
                v["invoice_in_month"] = lambda value=amount: value
                break
    except Exception as e:  # noqa: BLE001
        log.warning("发票真值读取失败（invoice_in_month 跳过）：%s", e)

    # 库存
    try:
        stock = db.stock_summary()
        low = len(stock.get("low_stock") or [])
        expiring = len(stock.get("expiring") or [])
        v["low_stock_count"] = lambda value=low: value
        v["expiring_count"] = lambda value=expiring: value
    except Exception as e:  # noqa: BLE001
        log.warning("库存真值读取失败（low_stock/expiring 跳过）：%s", e)

    # 熟客（数量 / 到店次数 / 最久未到天数）
    try:
        rows = db.list_customers() or []
        visits = {str(r.get("name") or ""): int(r.get("order_count") or 0)
                  for r in rows if str(r.get("name") or "")}
        v["customer_count"] = lambda value=len(rows): value
        v["customer_visits"] = lambda value=visits: value
        oldest = 0
        today_date = date.today()
        for r in rows:   # 与 skill_cards.build_context 同一算法：最大"距今天数"，上限 999
            last = str(r.get("last_visit") or "").strip()
            if not last:
                continue
            try:
                days = (today_date - datetime.strptime(last[:10], "%Y-%m-%d").date()).days
            except ValueError:
                continue
            if days > 0:
                oldest = max(oldest, min(days, 999))
        v["stale_customer_days"] = lambda value=oldest: value
    except Exception as e:  # noqa: BLE001
        log.warning("熟客真值读取失败（customer_* 跳过）：%s", e)

    # 单店档案 → 现金跑道（口径与 skill_cards 一致）
    try:
        profiles = db.list_store_profiles() or []
    except Exception as e:  # noqa: BLE001
        log.warning("店档案读取失败（cash_runway/breakeven 跳过）：%s", e)
        profiles = None
    if profiles:
        cash = float(profiles[0].get("cash_on_hand") or 0)
        month_expense = float((month or {}).get("expense") or 0)
        runway = round(cash / max(1.0, month_expense or 1.0), 1)
        v["cash_runway_months"] = lambda value=runway: value

    # 账本反推的毛利率 / 保本日销
    try:
        stats = db.store_ledger_stats() or {}
    except Exception as e:  # noqa: BLE001
        log.warning("账本反推真值读取失败（gross_margin/breakeven 跳过）：%s", e)
        stats = {}
    if stats.get("gross_margin") is not None:
        margin = float(stats["gross_margin"])
        v["gross_margin"] = lambda value=margin: value
    breakeven = _breakeven_daily(stats, profiles)
    if breakeven is not None:
        v["breakeven_daily"] = lambda value=breakeven: value
    return v


def _breakeven_daily(stats: dict, profiles):
    """保本日销：账本反推只给日销/毛利率，保本线本身按 routers/store.py 的口径复算。

    复用 store.calc_store_model（不新造公式）；没有店档案或固定成本口径时返回 None，
    调用方据此**不产生**这个 key，而不是塞一个 0。
    """
    if not profiles:
        return None
    p = profiles[0]
    try:
        import store as storelib
        res = storelib.calc_store_model(
            daily_revenue=float(stats.get("daily_revenue") or 0),
            gross_margin=p.get("gross_margin"),
            rent=p.get("rent") or 0,
            salary=p.get("salary") or 0,
            utilities=p.get("utilities") or 0,
            total_investment=p.get("total_investment") or 0,
            cash_on_hand=p.get("cash_on_hand") or 0,
            traffic=p.get("traffic") or "一般",
            competitor=p.get("competitor") or "一般",
            biz_type=p.get("biz_type") or "餐饮")
        value = (res.get("model") or {}).get("break_even_day")
        if value is None or value == float("inf"):
            return None
        return round(float(value), 1)
    except Exception as e:  # noqa: BLE001
        log.warning("保本日销口径复算失败（不产生 breakeven_daily）：%s", e)
        return None


# ---------------- 运行期核验 ----------------

def _to_number(text):
    if text is None:
        return None
    try:
        return float(str(text).strip().replace(",", "").rstrip("%").strip())
    except (TypeError, ValueError):
        return None


def _key_in_text(text, verifiers) -> str:
    """从文本里找 verifiers 的 key（如 subject='cash_runway_months' → 命中）。"""
    if not text:
        return ""
    hits = [k for k in (verifiers or {}) if k and k in str(text)]
    if not hits:
        return ""
    return sorted(hits, key=len, reverse=True)[0]


def _parse_expected(asset, verifiers):
    """从证据里解析"写入时值"。

    优先 `key=value`（value 支持千分位与 %，且 key 必须在 verifiers 里）；
    只有无 key 证据（如"日销 1320 元"）时，按 asset.subject 里的 key 试匹配。
    返回 (来源名, 写入时值)；解析不出就给 (name 或 "", None)。
    """
    raw = asset.get("evidence") or []
    if isinstance(raw, str):
        raw = [raw]
    keyed_fallback = None
    for line in raw:
        text = str(line).strip()
        if not text:
            continue
        m = _KEY_VALUE_RE.match(text)
        if m:
            name, expected = m.group(1), _to_number(m.group(2))
            if expected is None:
                continue
            if name in (verifiers or {}):
                return name, expected
            if keyed_fallback is None:
                keyed_fallback = (name, expected)
            continue
        m2 = _BARE_NUMBER_RE.search(text)
        if m2:
            expected = _to_number(m2.group(1))
            if expected is None:
                continue
            name = (_key_in_text(asset.get("subject") or "", verifiers)
                    or _key_in_text(asset.get("statement") or "", verifiers))
            if name:
                return name, expected
    if keyed_fallback is not None:
        return keyed_fallback      # 有 key 但没真值来源 → 调用方判 unknown
    return "", None


def check_volatility(asset, verifiers=None) -> dict:
    """把一条资产的"写入时值"和运行期真值对一遍。

    返回 {name, status: ok|drift|unknown, found, expected, drift_ratio}。
    **unknown 不等于 ok**：没真值来源就说不清楚，不许当结论用。
    """
    from knowledge_assets import source_tolerance
    verifiers = _default_verifiers() if verifiers is None else verifiers
    name, expected = _parse_expected(asset, verifiers)
    blank = {"name": name or "", "status": "unknown", "found": None,
             "expected": expected, "drift_ratio": None}
    if not name or expected is None:
        return blank
    fn = (verifiers or {}).get(name)
    if fn is None:
        return blank
    try:
        found = fn()
    except Exception as e:  # noqa: BLE001 —— 取数失败即 unknown，不算"没漂移"
        log.warning("运行期取数失败（%s → unknown）：%s", name, e)
        return blank
    if found is None or isinstance(found, bool) or not isinstance(found, (int, float)):
        return {"name": name, "status": "unknown", "found": found,
                "expected": expected, "drift_ratio": None}
    found = float(found)
    ratio = abs(found - expected) / max(abs(expected), 1e-9)
    tolerance = source_tolerance(asset.get("volatility") or "volatile")
    return {"name": name, "status": "drift" if ratio > tolerance else "ok",
            "found": found, "expected": expected, "drift_ratio": round(ratio, 6)}


def _fmt_num(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(round(number, 4))


def _drift_note(result: dict) -> str:
    """把核验结果写成人话（漂移必须说清楚"写入时 vs 现在"）。"""
    if result.get("status") == "drift":
        return (f"真值已变：写入时{result.get('name')}={_fmt_num(result.get('expected'))}，"
                f"现在={_fmt_num(result.get('found'))}")
    if result.get("status") == "unknown":
        if result.get("name"):
            return f"暂时核验不了（{result['name']} 没有可对比的真值来源），先别当结论用"
        return "暂时核验不了（这条知识没有可对比的真值来源），先别当结论用"
    return ""


def verify_asset(asset_id=None, *, limit=None, persist=True) -> dict:
    """对 active 且 volatility=='volatile' 的资产重算真值并（默认）写回核验结果。

    asset_id 给定时只验这一条（调用方已确认要验它）。
    persist=False 时只算不写，供测试与"预览核验"用。
    """
    import knowledge_assets as ka
    verifiers = _default_verifiers()
    if asset_id:
        one = ka.get_asset(asset_id)
        assets = [one] if one else []
    else:
        try:
            cap = max(1, min(int(limit or 50), 500))
        except (TypeError, ValueError):
            cap = 50
        from db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_assets WHERE state='active' "
                "AND volatility='volatile' ORDER BY updated_at DESC, version DESC LIMIT ?",
                (cap,)).fetchall()
        assets = [ka._row_dict(r) for r in rows]

    items, counts = [], {"ok": 0, "drift": 0, "unknown": 0}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates = []
    for asset in assets:
        result = check_volatility(asset, verifiers)
        note = _drift_note(result)
        status = result.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        items.append({"asset_id": asset.get("asset_id", ""), "subject": asset.get("subject", ""),
                      "statement": asset.get("statement", ""), "name": result.get("name", ""),
                      "status": status, "found": result.get("found"),
                      "expected": result.get("expected"),
                      "drift_ratio": result.get("drift_ratio"), "drift_note": note})
        updates.append((now, 1 if status == "ok" else 0, note, asset.get("asset_id", "")))
    if persist and updates:
        # 一次连接 + executemany：原先逐条开连接写回，N 条资产要 N 次连接（N+1 写放大）。
        from db import get_conn
        with get_conn() as conn:
            conn.executemany(
                "UPDATE knowledge_assets SET verified_at=?, verify_ok=?, drift_note=? "
                "WHERE asset_id=?", updates)
    return {"checked": len(items), "ok": counts["ok"], "drift": counts["drift"],
            "unknown": counts["unknown"], "items": items, "checked_at": now}
