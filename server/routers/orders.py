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
    """一句话记账：AI解析（含收支分类） + 熟客自动归档 + 生成借贷凭证"""
    parsed = ai.parse_transaction(data.text)
    customer = data.customer or parsed.get("customer", "")
    cid = None
    is_new = False
    if customer:
        cid, is_new = db.find_or_create_customer(
            customer, tags=parsed.get("tags", ""), favorite=parsed.get("item", ""))
    amount = data.amount if data.amount is not None else parsed.get("amount")
    amount_missing = amount is None
    trans_type = parsed.get("trans_type", "income")
    category = parsed.get("category", "")
    # 防御：AI 可能输出未知分类，落到凭证前必须校验，否则用关键词兜底
    if not category or not is_known_category(category):
        category, trans_type = db.detect_category(data.text)
    tid, voucher = db.add_transaction(
        cid, parsed.get("item", "") or data.text, amount,
        trans_type=trans_type, category=category,
        counterparty=customer, note=parsed.get("note", ""))
    # 安全护栏：边界场景优先，其次大额检测
    safety_warning = taxcalc.detect_boundary(data.text) or taxcalc.check_amount_guard(amount)
    log.info("order created tid=%s type=%s category=%s amount=%s customer=%s missing=%s",
             tid, trans_type, category, amount, customer or "-", amount_missing)
    return {
        "order_id": tid,
        "parsed": parsed,
        "customer_id": cid,
        "customer_new": cid and is_new,
        "amount_missing": amount_missing,
        "voucher": voucher,
        "friendly_category": db.FRIENDLY_NAMES.get(category, category),
        "summary": db.today_summary(),
        "safety_warning": safety_warning,
    }


@router.get("/orders/today")
def orders_today():
    return db.today_summary()


@router.get("/orders/monthly")
def orders_monthly(year: int | None = None, month: int | None = None):
    """月度收支汇总 + 分类明细（查账）"""
    return db.monthly_summary(year, month)


@router.get("/vouchers")
def vouchers(limit: int = 50):
    """凭证列表（复式记账，借贷分录）"""
    return db.list_vouchers(limit)


@router.get("/transactions")
def transactions(year: int | None = None, month: int | None = None, limit: int = 100):
    """月度交易流水（大白话分类名），默认当月"""
    return db.list_transactions(year, month, limit)


@router.post("/orders/insights")
def order_insights(data: InsightIn):
    """AI 经营洞察：读取月度收支 → 读取上次分析(domain_context) → AI 生成洞察 → 落盘"""
    monthly = db.monthly_summary(data.year, data.month)
    prev = db.get_domain_context("ledger", "monthly_insights")
    prev_text = prev["value"] if prev else ""
    text = ai.generate_insights(monthly, prev_text)
    db.set_domain_context("ledger", "monthly_insights", text)
    return {"insights": text, "monthly": monthly, "ai_used": ai.ai_available()}