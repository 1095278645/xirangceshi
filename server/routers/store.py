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


@router.get("/breakeven/today")
def breakeven_today():
    """统一保本线输出口径：今天已卖多少、离保本/舒服线还差多少。

    复用规则（不新造公式）：
      - 今日已卖：db.today_summary()
      - 本月日均：db.store_ledger_stats()（最近有收入月份）
      - 保本线/目标线：最近一份店档案喂给 store.calc_store_model()
    没有店档案时不硬算一个假数字，返回 configured=false 引导去补口径。
    """
    today = db.today_summary()
    stats = db.store_ledger_stats()
    profiles = db.list_store_profiles()
    today_income = round(float(today.get("income") or 0), 2)
    month_daily = stats.get("daily_revenue")
    month_daily = None if month_daily is None else round(float(month_daily), 2)

    if not profiles:
        return {
            "configured": False,
            "today_income": today_income,
            "month_daily_avg": month_daily,
            "text": "先在『单店』页算一次保本线，以后首页天天帮你盯今天还差多少。",
            "action": "去单店页算保本线",
        }

    p = profiles[0]
    res = storelib.calc_store_model(
        daily_revenue=(month_daily if month_daily is not None else today_income),
        gross_margin=p.get("gross_margin"),
        rent=p.get("rent") or 0,
        salary=p.get("salary") or 0,
        utilities=p.get("utilities") or 0,
        total_investment=p.get("total_investment") or 0,
        cash_on_hand=p.get("cash_on_hand") or 0,
        traffic=p.get("traffic") or "一般",
        competitor=p.get("competitor") or "一般",
        biz_type=p.get("biz_type") or "餐饮",
    )
    be = res["model"]["break_even_day"]
    target = res["model"]["target_day"]
    be = None if be == float("inf") else round(float(be), 1)
    target = None if target == float("inf") else round(float(target), 1)

    if be is None:
        level, text = "none", "固定成本口径还没法算，先在『单店』页补齐房租/人工/水电。"
    elif today_income < be:
        gap = round(be - today_income, 1)
        level, text = "danger", f"今天已卖 {today_income:,.0f} 元，还差 {gap:,.0f} 元才保本。"
    elif target is not None and (month_daily or 0) < target:
        gap = round(target - (month_daily or 0), 1)
        level, text = "warn", f"今天已保本；本月日均离『舒服点』还差 {gap:,.0f} 元/天。"
    else:
        level, text = "ok", "今天已站稳保本线，也过了舒服线。别松劲，先把现金垫备厚。"

    return {
        "configured": True,
        "profile": {"id": p.get("id"), "name": p.get("name"), "biz_type": p.get("biz_type")},
        "estimated_margin": p.get("gross_margin") is None,
        "today_income": today_income,
        "month_daily_avg": month_daily,
        "break_even_day": be,
        "target_day": target,
        "today_gap": None if be is None else round(max(be - today_income, 0), 1),
        "month_gap_vs_target": None if target is None else round(max(target - (month_daily or 0), 0), 1),
        "level": level,
        "text": text,
        "model": res["model"],
    }


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