"""统一 AI 洞察服务：入口收敛、同日缓存与友好降级。"""
from __future__ import annotations

import hashlib
import json
from datetime import date

import ai
import db
import store as storelib
import tax as taxcalc

from team_domain_copy import _copy_degraded
from team_domain_store import _store_diagnosis_degraded


def _fingerprint(scene: str, payload: dict) -> str:
    raw = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True,
                     default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_key(scene: str, payload: dict) -> str:
    return f"unified:{scene}:{_fingerprint(scene, payload)}:{date.today():%Y-%m-%d}"


def _copy(payload: dict) -> dict:
    context_parts = []
    for domain, key in (("ledger", "daily_review"), ("store", "diagnosis")):
        item = db.get_domain_context(domain, key)
        if item and item.get("value"):
            context_parts.append(str(item["value"])[:200])
    text, process, variants = ai.generate_copy(
        payload.get("shop_name", "我的小店"), payload.get("scene", "今日营业"),
        payload.get("extra", ""), payload.get("customer_name", ""),
        " | ".join(x for x in context_parts if x), return_process=True)
    return {"text": text, "team": process, "variants": variants,
            "gene_id": (process or {}).get("gene_id")}


def _monthly(payload: dict) -> dict:
    monthly = db.monthly_summary(payload.get("year"), payload.get("month"))
    try:
        days = db.store_ledger_stats(payload.get("year"), payload.get("month")).get("active_days")
    except Exception:  # noqa: BLE001
        days = None
    text = ai.generate_insights(monthly, "", days)
    return {"insights": text, "monthly": monthly,
            "ai_used": ai.ai_available(), "cached": False}


def _tax(payload: dict) -> dict:
    revenue = float(payload.get("quarterly_revenue") or 0)
    vat_result = taxcalc.calc_vat(revenue)
    text = ai.generate_tax_advice(revenue, vat_result, "")
    return {"advice": text, "vat_result": vat_result,
            "ai_used": ai.ai_available(), "cached": False}


def _customer(payload: dict) -> dict:
    cid = int(payload.get("customer_id") or 0)
    customer = db.get_customer(cid)
    if not customer:
        raise ValueError("客户不存在")
    text = ai.generate_customer_insight(customer, customer.get("transactions", []))
    return {"insight": text, "customer": customer, "ai_used": ai.ai_available()}


def _store(payload: dict) -> dict:
    model_result = storelib.calc_store_model(
        daily_revenue=float(payload.get("daily_revenue") or 0),
        gross_margin=payload.get("gross_margin"),
        rent=float(payload.get("rent") or 0),
        salary=float(payload.get("salary") or 0),
        utilities=float(payload.get("utilities") or 0),
        total_investment=float(payload.get("total_investment") or 0),
        cash_on_hand=float(payload.get("cash_on_hand") or 0),
        traffic=payload.get("traffic", "一般"),
        competitor=payload.get("competitor", "一般"),
        biz_type=payload.get("biz_type", "餐饮"),
    )
    prev = db.get_domain_context("store", "diagnosis")
    text, process = ai.generate_store_diagnosis(
        model_result, prev["value"] if prev else "", return_process=True)
    db.set_domain_context("store", "diagnosis", text)
    return {"diagnosis": text, "model": model_result,
            "ai_used": ai.ai_available(), "team": process}


_HANDLERS = {"copy": _copy, "monthly": _monthly, "tax": _tax,
             "customer": _customer, "store": _store}


def _fallback(scene: str, payload: dict, message: str) -> dict:
    prefix = "AI 暂时不可用，已用本地规则兜底。"
    if scene == "copy":
        text = _copy_degraded(payload.get("shop_name", "我的小店"),
                              payload.get("scene", "今日营业"),
                              payload.get("extra", ""))
        return {"text": f"{prefix}{text}", "variants": [text], "team": {},
                "degraded": True, "message": message}
    if scene == "monthly":
        summary = db.monthly_summary(payload.get("year"), payload.get("month"))
        return {"insights": f"{prefix}本月收入 {summary['income']:.0f} 元，支出 {summary['expense']:.0f} 元。",
                "monthly": summary, "ai_used": False, "degraded": True,
                "message": message}
    if scene == "tax":
        revenue = float(payload.get("quarterly_revenue") or 0)
        vat = taxcalc.calc_vat(revenue)
        due = 0 if vat.get("exempt") else vat.get("vat", 0)
        return {"advice": f"{prefix}本季度增值税约 {due:.0f} 元，记得在季度结束后次月 15 日前申报。",
                "vat_result": vat, "ai_used": False, "degraded": True,
                "message": message}
    if scene == "customer":
        customer = db.get_customer(int(payload.get("customer_id") or 0)) or {}
        txns = customer.get("transactions", []) if customer else []
        total = sum(float(x.get("amount") or 0) for x in txns)
        return {"insight": f"{prefix}{customer.get('name', '顾客')}共有 {len(txns)} 笔消费，累计 {total:.0f} 元。",
                "customer": customer, "ai_used": False, "degraded": True,
                "message": message}
    if scene == "store":
        model = storelib.calc_store_model(
            daily_revenue=float(payload.get("daily_revenue") or 0),
            gross_margin=payload.get("gross_margin"), rent=float(payload.get("rent") or 0),
            salary=float(payload.get("salary") or 0), utilities=float(payload.get("utilities") or 0),
            total_investment=float(payload.get("total_investment") or 0),
            cash_on_hand=float(payload.get("cash_on_hand") or 0),
            traffic=payload.get("traffic", "一般"), competitor=payload.get("competitor", "一般"),
            biz_type=payload.get("biz_type", "餐饮"))
        return {"diagnosis": f"{prefix}{_store_diagnosis_degraded(model)}",
                "model": model, "ai_used": False, "degraded": True,
                "message": message}
    return {"degraded": True, "message": message}


def generate(scene: str, payload: dict, refresh: bool = False) -> dict:
    handler = _HANDLERS.get(scene)
    if not handler:
        raise ValueError("未知洞察类型")
    key = _cache_key(scene, payload)
    if not refresh:
        hit = db.get_domain_context("insight", key)
        if hit and isinstance(hit.get("value"), dict):
            result = hit["value"]
            result["cached"] = True
            return result
    try:
        result = handler(payload)
    except Exception:  # noqa: BLE001
        return _fallback(scene, payload, "AI 服务暂时不可用，请稍后重试。")
    result.setdefault("cached", False)
    if not result.get("degraded"):
        db.set_domain_context("insight", key, result)
    return result
