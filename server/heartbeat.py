"""heartbeat.py — 每日心跳复盘（对标 TinyAGI 心跳：主动生成 + 复盘落盘）

把「账本流水 + 单店引擎」整合为掌柜的每日复盘，落盘到
domain_context(ledger, daily_review)，供前端/日报/推送复用。

- 今日收支：today_summary
- 本月概览：monthly_summary
- 账本反推：store_ledger_stats（最近有营业月份，算日销/毛利率参考）
- 单店诊断：取最近保存的店档案跑 calc_store_model，得出「掌柜一句话」

main.py 的 asyncio 定时任务周期调用 generate_daily_review() 即可。
"""
import store as storelib

__all__ = ["generate_daily_review", "daily_review_text"]


def _latest_profile():
    """取最近一个店档案；没有返回 None"""
    from db import list_store_profiles
    rows = list_store_profiles()
    return rows[0] if rows else None


def _one_liner(profile):
    """基于店档案跑单店引擎，生成「掌柜一句话」"""
    if not profile:
        return ("还没建过店档案：去『单店』页把房租/成本/投资填上，"
                "我才能帮你盯保本线、算回本周期。")
    try:
        res = storelib.calc_store_model(
            gross_margin=profile.get("gross_margin"),
            rent=profile.get("rent") or 0,
            salary=profile.get("salary") or 0,
            utilities=profile.get("utilities") or 0,
            total_investment=profile.get("total_investment") or 0,
            cash_on_hand=profile.get("cash_on_hand") or 0,
            traffic=profile.get("traffic") or "一般",
            competitor=profile.get("competitor") or "一般",
            biz_type=profile.get("biz_type") or "餐饮",
        )
    except Exception:  # noqa: BLE001
        return "单店诊断暂时算不出来，稍后再试。"
    verdict = res["overall"]["level"]
    return f"[单店 {verdict}] {res['advice']}"


def _ledger_line():
    """从账本反推最近营业月参考；无营业记录返回 None"""
    from db import store_ledger_stats
    try:
        s = store_ledger_stats()
    except Exception:  # noqa: BLE001
        return None
    if s.get("daily_revenue") is None:
        return None
    extra = ""
    if s.get("gross_margin"):
        extra = f"，毛利率约 {round(s['gross_margin'] * 100)}%"
    return (f"最近有营业的 {s['period']} 月：日均流水约 {s['daily_revenue']:,.0f} 元"
            f"{extra}（收入 {s['income_total']:,.0f} 元）")


def generate_daily_review():
    """生成并落盘今日复盘，返回复盘文本。"""
    from db import today_summary, monthly_summary, set_domain_context
    today = today_summary()
    month = monthly_summary()

    parts = [
        f"今日收 {today['income']:,.0f} 元 / 支 {today['expense']:,.0f} 元"
        f"（{today['cnt']} 笔，净 {today['balance']:,.0f} 元）",
        f"本月收 {month['income']:,.0f} 元 / 支 {month['expense']:,.0f} 元"
        f"（净 {month['balance']:,.0f} 元）",
    ]
    ledger_line = _ledger_line()
    if ledger_line:
        parts.append(ledger_line)
    parts.append(_one_liner(_latest_profile()))

    text = "｜".join(parts)
    set_domain_context("ledger", "daily_review", text)
    return text


def daily_review_text():
    """读取最近一次落盘的今日复盘；无则返回 None"""
    from db import get_domain_context
    item = get_domain_context("ledger", "daily_review")
    return item["value"] if item else None