"""单店经营模型（勇哥方法论泛化：保本线先行）"""
from fastapi import APIRouter

import ai
import db
import store as storelib
from schemas import StoreModelIn

router = APIRouter(prefix="/api", tags=["store"])


@router.get("/store/presets")
def store_presets():
    """业态预设：参考毛利率区间 + 经营提示"""
    return {
        "presets": [
            {"key": k, "name": v["name"], "margin_range": list(v["margin_range"]),
             "margin_default": v["margin_default"], "note": v["note"]}
            for k, v in storelib.BUSINESS_PRESETS.items()
        ],
        "rule": "保本线是店的命线：日销低于保本线，开门一天亏一天；低于目标线，白忙不赚钱",
    }


@router.post("/store/model")
def store_model(data: StoreModelIn):
    """单店模型计算：保本线 + 目标日销 + 回本周期 + 现金流 + 三维诊断"""
    return storelib.calc_store_model(
        daily_revenue=data.daily_revenue,
        gross_margin=data.gross_margin,
        rent=data.rent,
        salary=data.salary,
        utilities=data.utilities,
        total_investment=data.total_investment,
        cash_on_hand=data.cash_on_hand,
        traffic=data.traffic,
        competitor=data.competitor,
        biz_type=data.biz_type,
    )


@router.get("/store/from-ledger")
def store_from_ledger(year: int | None = None, month: int | None = None):
    """从账本真实流水反推单店输入：实际日销 + 毛利率（不传年月自动取最近有收入的月份）"""
    return db.store_ledger_stats(year, month)


@router.post("/store/diagnosis")
def store_diagnosis(data: StoreModelIn):
    """AI 经营诊断：跑单店模型 → 读取上次诊断(domain_context) → AI 生成 → 落盘"""
    model_result = storelib.calc_store_model(
        daily_revenue=data.daily_revenue,
        gross_margin=data.gross_margin,
        rent=data.rent,
        salary=data.salary,
        utilities=data.utilities,
        total_investment=data.total_investment,
        cash_on_hand=data.cash_on_hand,
        traffic=data.traffic,
        competitor=data.competitor,
        biz_type=data.biz_type,
    )
    prev = db.get_domain_context("store", "diagnosis")
    prev_text = prev["value"] if prev else ""
    text, process = ai.generate_store_diagnosis(model_result, prev_text, return_process=True)
    db.set_domain_context("store", "diagnosis", text)
    return {"diagnosis": text, "model": model_result, "ai_used": ai.ai_available(),
            "team": process}