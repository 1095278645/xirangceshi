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


def _growth(payload: dict) -> dict:
    """增长动作：拉新 / 复购 / 选品提价（把"守"扩展到"攻"，OPC：帮一人公司多赚钱）。

    无 Key 时走**规则化兜底**，同样给出"具体到对象与做法"的三条动作（不是空话模板）。
    """
    from db import get_conn, list_products, monthly_summary, store_ledger_stats
    monthly = monthly_summary(payload.get("year"), payload.get("month"))
    try:
        gm = store_ledger_stats(payload.get("year"), payload.get("month")).get("gross_margin")
    except Exception:  # noqa: BLE001
        gm = None
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.favorite, COUNT(t.id) AS n, "
            "COALESCE(SUM(CASE WHEN t.trans_type='income' THEN t.amount ELSE 0 END),0) AS amt, "
            "MAX(t.created_at) AS last_seen "
            "FROM customers c LEFT JOIN transactions t ON t.customer_id=c.id "
            "AND COALESCE(t.status,'active') != 'voided' GROUP BY c.id").fetchall()
    cust = [dict(r) for r in rows]
    top = sorted(cust, key=lambda c: -(c.get("amt") or 0))[:5]
    dormant = sorted([c for c in cust if (c.get("n") or 0) >= 2],
                     key=lambda c: str(c.get("last_seen") or ""))[:5]
    prods = list_products() or []
    slow = sorted([p for p in prods if (p.get("stock_qty") or 0) > 0],
                  key=lambda p: (p.get("unit_cost") or 0))[:5]

    basis = {"monthly": monthly, "gross_margin": gm, "top_customers": top,
             "dormant_customers": dormant, "slow_products": slow}
    if not ai.ai_available():
        names = [c["name"] for c in dormant[:3]] or [c["name"] for c in top[:3]]
        who = "、".join(names) if names else "老顾客"
        first = names[0] if names else "老顾客"
        acts = [
            f"① 拉新：让「{first}」帮你带一位新客——老带新双方各减 2 元；今天就在她常来的时段提一句。",
            f"② 复购：{who} 有阵子没来了，各发一句问候（问问上次买的那口还满意吗），顺手约个到店时间。",
            (f"③ 选品：库存里的「{slow[0]['name']}」占着资金，做「{slow[0]['name']} + 招牌单品」组合价先走动。"
             if slow else "③ 选品：挑店里毛利最高的一款做「+1 元升级」搭售，把客单价抬起来。"),
        ]
        return {"actions": "\n".join(acts), "ai_used": False, "basis": basis}

    brief = (f"本月收入 {monthly['income']:.0f} 元 / 支出 {monthly['expense']:.0f} 元；"
             f"毛利率 {'未知' if gm is None else format(gm, '.1%')}。\n"
             "消费最多的熟客：" + "、".join(f"{c['name']}（{c['amt']:.0f}元/{c['n']}次）" for c in top) + "\n"
             "最近的熟客（按最近一次消费排序，越靠前越久没来）：" + ("、".join(c["name"] for c in dormant) or "无") + "\n"
             "库存商品：" + ("、".join(f"{p['name']}（存{p.get('stock_qty')}）" for p in slow) or "未建库存档案"))
    return {"actions": ai.generate_growth_actions(brief), "ai_used": True, "basis": basis}


_HANDLERS["growth"] = _growth   # 定义后再登记（避免名字未定义）


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
    if scene == "growth":
        return {"actions": f"{prefix}先把最久没来的熟客挨个问候一遍，再挑一款库存做组合价。",
                "ai_used": False, "degraded": True, "message": message}
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
