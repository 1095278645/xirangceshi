"""heartbeat.py — 每日心跳复盘（多 agent 掌柜 + 落盘）

把「全店经营快照 + 多 agent 编排」整合为掌柜的每日复盘，落盘到
domain_context(ledger, daily_review)，供前端/日报/推送复用。

流程：
  1. shop_snapshot.build_snapshot() 汇总全店事实
     （账目 / 熟客 / 库存 / 赊账预算 / 发票 / 报税 / 账目更正）
  2. review 域的五位伙计各管一摊并行发言（账房 / 熟客 / 采买 / 税务 / 监察）
  3. 掌柜裁决融合，只挑今天最该动手的一两件
  4. 复盘文本与快照原文分别落盘（快照供前端展开"掌柜看到的事实"）
无 API Key 时走规则降级；团队编排异常时退回基础拼装，保证复盘永远有内容。

- 今日/本月收支：today_summary / monthly_summary
- 账本反推：store_ledger_stats
- 单店诊断：calc_store_model（兜底文本用）
- 进化检查：每日检查经验晋升 / 基因抑制 / 技能蒸馏

main.py 的 asyncio 定时任务周期调用 generate_daily_review() 即可。
"""
import logging

import store as storelib

log = logging.getLogger("heartbeat")

__all__ = ["generate_daily_review", "daily_review_text", "daily_snapshot_text",
           "evolution_daily_check"]


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
    """生成并落盘今日复盘，返回复盘文本。

    这里从"拼三个数字"升级为**多 agent 掌柜复盘**：
      1. shop_snapshot 汇总全店经营事实（账目/熟客/库存/赊账/发票/税/账目更正）
      2. review 域的五位伙计各管一摊并行发言（账房/熟客/采买/税务/监察）
      3. 掌柜裁决融合：只挑今天最该动手的一两件
    同时把快照原文落盘（domain_context 的 shop_snapshot），前端可展开看"掌柜看到的原始事实"。
    无 API Key 时走规则降级，业务文本依然可用。
    """
    import shop_snapshot
    from db import today_summary, monthly_summary, set_domain_context
    import team_domains

    snapshot = shop_snapshot.build_snapshot()
    set_domain_context("ledger", "shop_snapshot", snapshot)

    try:
        text = team_domains.generate_daily_review(snapshot)
    except Exception as e:  # noqa: BLE001 —— 团队编排失败退回拼装版，保证复盘不空
        log.warning("掌柜团队复盘失败，退回基础拼装：%s", e)
        text = _basic_review_fallback()

    if not (text or "").strip():
        text = _basic_review_fallback()
    set_domain_context("ledger", "daily_review", text)
    return text


def _basic_review_fallback() -> str:
    """最朴素的拼装版复盘（团队编排不可用时的兜底，保证永远有内容）。"""
    from db import today_summary, monthly_summary
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
    return "｜".join(parts)


def daily_review_text():
    """读取最近一次落盘的今日复盘；无则返回 None"""
    from db import get_domain_context
    item = get_domain_context("ledger", "daily_review")
    return item["value"] if item else None


def daily_snapshot_text():
    """读取最近一次落盘的全店快照（掌柜看到的事实）；无则返回 None"""
    from db import get_domain_context
    item = get_domain_context("ledger", "shop_snapshot")
    return item["value"] if item else None


def evolution_daily_check():
    """每日进化检查：经验晋升 / 基因抑制 / 技能蒸馏。
    纯本地算法，不依赖 AI API。返回检查结果摘要。"""
    import team_domains
    import team_evolution
    import evolution

    results = {"promoted": [], "suppressed": [], "distilled": []}

    # 1. 确保初始基因已入库
    try:
        team_evolution.seed_initial_genes()
    except Exception as e:  # noqa: BLE001
        results.setdefault("errors", []).append(f"seed: {e}")

    # 2. 遍历所有注册域，执行进化检查
    for domain in team_domains.list_team_domains():
        cfg = team_domains.TEAM_DOMAINS.get(domain, {})
        evo = cfg.get("evolution", {})
        if not evo.get("enabled"):
            continue

        try:
            # 经验晋升
            promoted = evolution.promote_learning(domain)
            if promoted:
                results["promoted"].extend(
                    [p.get("pattern_key", p.get("id")) for p in promoted])

            # 基因抑制检查
            suppressed = evolution.check_and_suppress(domain)
            if suppressed:
                results["suppressed"].extend(suppressed)

            # 技能蒸馏
            distilled = evolution.distill_skill(domain)
            if distilled:
                results["distilled"].append(distilled.get("gene_id"))
        except Exception as e:  # noqa: BLE001
            results.setdefault("errors", []).append(f"{domain}: {e}")

    return results