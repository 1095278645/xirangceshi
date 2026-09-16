"""记账 / 流水 / 凭证 / 月度汇总（查账）"""
import logging

from fastapi import APIRouter

import ai
import db
import tax as taxcalc
from categories import is_known_category
from schemas import OrderIn, InsightIn

log = logging.getLogger("orders")
router = APIRouter(prefix="/api", tags=["orders"])

@router.post("/orders")
def create_order(data: OrderIn):
    """一句话记账：AI解析 + 熟客归档 + 借贷凭证"""
    parsed = ai.parse_transaction(data.text)
    customer = data.customer or parsed.get("customer", "")
    cid = None
    is_new = False
    if customer:
        # set_favorite=False：这里传的是"本笔买了什么"，不是"常点什么"。
        # 若让它覆盖，每记一笔账熟客的常点就变一次（实测会把
        # "肉包,豆浆" 冲成 "两个肉包一杯豆浆"）。新客户仍会用它做首次学习。
        cid, is_new = db.find_or_create_customer(
            customer, tags=parsed.get("tags", ""), favorite=parsed.get("item", ""),
            set_favorite=False)
    amount = data.amount if data.amount is not None else parsed.get("amount")
    amount_missing = amount is None
    trans_type = parsed.get("trans_type", "income")
    category = parsed.get("category", "")
    if not category or not is_known_category(category):
        category, trans_type = db.detect_category(data.text)
    tid, voucher = db.add_transaction(
        cid, parsed.get("item", "") or data.text, amount,
        trans_type=trans_type, category=category,
        counterparty=customer, note=parsed.get("note", ""))
    safety_warning = taxcalc.detect_boundary(data.text) or taxcalc.check_amount_guard(amount)
    log.info("order created tid=%s type=%s category=%s amount=%s customer=%s missing=%s",
             tid, trans_type, category, amount, customer or "-", amount_missing)
    return {
        "order_id": tid, "parsed": parsed,
        "customer_id": cid, "customer_new": cid and is_new,
        "amount_missing": amount_missing, "voucher": voucher,
        "friendly_category": db.FRIENDLY_NAMES.get(category, category),
        "summary": db.today_summary(), "safety_warning": safety_warning,
    }

@router.get("/orders/today")
def orders_today():
    return db.today_summary()

@router.get("/orders/monthly")
def orders_monthly(year: int | None = None, month: int | None = None):
    return db.monthly_summary(year, month)

@router.get("/vouchers")
def vouchers(limit: int = 50):
    return db.list_vouchers(limit)

@router.get("/transactions")
def transactions(year: int | None = None, month: int | None = None, limit: int = 100):
    return db.list_transactions(year, month, limit)

def _insight_cache_key(period: str) -> str:
    """按月份分键缓存。原实现用固定的 monthly_insights 键，
    导致切换月份时会命中上个月的分析内容。"""
    return f"monthly_insights:{period}"


def _gen_insights(year, month):
    """调用 AI 生成并落盘（只在需要重新生成时执行）。"""
    monthly = db.monthly_summary(year, month)
    # 传实际营业天数：否则模型会按 30 天折算日均，与单店模型的
    # 「实际日销」（收入÷营业天数）口径不一致，同一场演示里自相矛盾。
    try:
        days = db.store_ledger_stats(year, month).get("active_days")
    except Exception:  # noqa: BLE001
        days = None
    prev = db.get_domain_context("ledger", _insight_cache_key(monthly["period"]))
    text = ai.generate_insights(monthly, prev["value"] if prev else "", days)
    db.set_domain_context("ledger", _insight_cache_key(monthly["period"]), text)
    return {"insights": text, "monthly": monthly, "ai_used": ai.ai_available(),
            "cached": False}


@router.post("/orders/insights")
def order_insights(data: InsightIn):
    """AI 经营洞察（按月缓存）。

    默认命中缓存直接返回，避免每次进账本页 / 切标签都触发一次 20~30 秒的
    AI 调用（账本页默认标签就是流水，加载完会自动请求洞察）。传 refresh=true
    才强制重新生成。
    """
    monthly = db.monthly_summary(data.year, data.month)
    period = monthly["period"]
    cache_key = _insight_cache_key(period)

    if not data.refresh:
        hit = db.get_domain_context("ledger", cache_key)
        text = hit["value"] if hit else ""
        # 旧版固定键的缓存：仅当它就是本月数据时才复用，避免张冠李戴
        if not text:
            legacy = db.get_domain_context("ledger", "monthly_insights")
            if legacy and (legacy.get("value") or "").startswith(period):
                text = legacy["value"]
        if text:
            return {"insights": text, "monthly": monthly,
                    "ai_used": ai.ai_available(), "cached": True,
                    "updated_at": (hit or {}).get("updated_at", "")}

    if not ai.ai_available():
        # 无 Key：走降级模板，不产生"缓存"概念上的困扰（内容每次一致）
        text = ai.generate_insights(monthly, "")
        return {"insights": text, "monthly": monthly, "ai_used": False, "cached": False}

    return _gen_insights(data.year, data.month)
