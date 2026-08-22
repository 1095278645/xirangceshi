"""evolution_growth.py — 技能蒸馏 + 经验晋升 + 任务前回顾（从 evolution.py 拆出）

职责：从历史执行中提炼新基因、晋升经验到永久记忆、任务前注入经验提醒。
依赖方向：evolution_growth → db_evolution + team_evolution（无循环依赖）。
"""
from datetime import datetime, timezone

import db_evolution as dbe
import team_evolution as te
from db_arch import set_domain_context, get_domain_context

# 技能蒸馏阈值（GenericAgent）
DISTILL_SUCCESS_COUNT = 7
DISTILL_HOURS_GAP = 24
DISTILL_SCORE_MULTIPLIER = 0.8

# 晋升规则阈值（self-improving-agent）
PROMOTE_RECURRENCE = 3
PROMOTE_DISTINCT_TASKS = 2
PROMOTE_DAYS_WINDOW = 30


# ---------------- 技能蒸馏 ----------------

def distill_skill(domain):
    """技能蒸馏：7/10 成功 + 24h 间隔 → 生成新 Gene + 更新 L1

    [DISTILLED] (domain) → new_gene | None
    """
    recent = dbe.get_recent_capsules(domain, limit=10)
    if len(recent) < 10:
        return None

    success_count = sum(1 for c in recent if c.get("user_adopted"))
    if success_count < DISTILL_SUCCESS_COUNT:
        return None

    last_distill = get_domain_context(domain, "last_distill_time")
    if last_distill and last_distill.get("value"):
        try:
            last_dt = datetime.fromisoformat(last_distill["value"])
            hours_gap = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if hours_gap < DISTILL_HOURS_GAP:
                return None
        except (ValueError, TypeError):
            pass

    successful = [c for c in recent if c.get("user_adopted")]
    common_signals = te.extract_common_signals(successful)
    common_content_pattern = te.extract_content_pattern(successful)

    gene_id = f"gene_distilled_{domain}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    new_gene = dbe.save_gene(
        gene_id=gene_id,
        domain=domain,
        trigger_signals=common_signals,
        system_prompt_addon=common_content_pattern,
        strategy_steps=None,
        confidence=0.5 * DISTILL_SCORE_MULTIPLIER,
        success_count=0,
        failure_count=0,
        status="active",
        category="reinforce",
        is_distilled=1,
    )

    dbe.log_event("gene_distilled", gene_id=gene_id, domain=domain,
                  details=f"distilled from {success_count}/10 successful capsules")

    te.update_insight_index(domain)
    set_domain_context(domain, "last_distill_time",
                       datetime.now(timezone.utc).isoformat())
    return new_gene


# ---------------- 经验晋升 ----------------

def promote_learning(domain):
    """检查某域的经验日志，满足晋升条件的提升到 domain_context

    [PROMOTED] (domain) → [promoted_items]
    晋升条件：recurrence_count >= 3 AND distinct_tasks >= 2 AND days_since_first <= 30
    """
    pending = dbe.get_pending_learnings(domain)
    promoted = []

    for item in pending:
        if item.get("recurrence_count", 0) < PROMOTE_RECURRENCE:
            continue

        metadata = item.get("metadata") or {}
        distinct_tasks = metadata.get("distinct_tasks", 1)
        if isinstance(distinct_tasks, list):
            distinct_count = len(distinct_tasks)
        else:
            distinct_count = int(distinct_tasks)

        if distinct_count < PROMOTE_DISTINCT_TASKS:
            continue

        first_seen = item.get("first_seen", "")
        if first_seen:
            try:
                first_dt = datetime.fromisoformat(first_seen)
                days_since = (datetime.now(timezone.utc) - first_dt).days
                if days_since > PROMOTE_DAYS_WINDOW:
                    continue
            except (ValueError, TypeError):
                pass

        key = f"promoted_{item['pattern_key'] or item['id']}"
        summary = (f"[晋升自经验日志] {item.get('pattern_key', '')}: "
                   f"{item.get('details', '')[:200]}")
        set_domain_context(domain, key, summary)
        dbe.promote_learning(item["id"])
        dbe.log_event("learning_promoted", domain=domain,
                      details=f"pattern={item.get('pattern_key')}, count={item['recurrence_count']}")
        promoted.append(item)

    if promoted:
        te.update_insight_index(domain)
    return promoted


# ---------------- 任务前回顾 ----------------

def review_injection(domain):
    """获取某域 pending 的经验条目，格式化为提示词注入文本

    返回注入文本（空字符串表示无待回顾条目）。
    """
    pending = dbe.get_pending_learnings(domain)
    if not pending:
        return ""

    lines = []
    for item in pending[:5]:
        pk = item.get("pattern_key", "")
        details = item.get("details", "")[:100]
        count = item.get("recurrence_count", 1)
        lines.append(f"· {pk}（出现{count}次）：{details}")

    return ("【历史经验提醒】以下是该域近期出现的问题模式，请避免：\n"
            + "\n".join(lines))
