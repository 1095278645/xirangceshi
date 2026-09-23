"""单店经营模型（勇哥方法论泛化：保本线先行）"""
from fastapi import APIRouter

import benchmark
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


@router.get("/store/benchmark")
def store_benchmark(biz_type: str = "餐饮", year: int | None = None, month: int | None = None):
    """同业基准（**示例/仿真值**）：本店指标 vs 同业态参考区间。

    诚实声明见 benchmark.py 与返回值里的 disclaimer —— 不含真实门店数据，
    接入脱敏聚合结果后接口契约不变。
    """
    stats = db.store_ledger_stats(year, month)
    from db_ledger import _ACTIVE_FILTER
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions "
            f"WHERE substr(created_at,1,7)=? AND {_ACTIVE_FILTER} "
            "AND trans_type='income' AND amount>0", (stats["period"],)).fetchone()
    cnt = int(row["c"] or 0)
    avg_ticket = round(float(stats["income_total"]) / cnt, 2) if cnt else None
    out = benchmark.compare(biz_type, stats, avg_ticket)
    out["period"] = stats["period"]
    return out