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

# 保守编辑硬约束（借鉴 SkillClaw 的 8 条，蒸馏必须遵守，防止 LLM 过度改写）
CONSERVATIVE_CONSTRAINTS = [
    "不得改变基因的核心触发信号集（trigger_signals 需包含源基因的核心信号）",
    "不得删除已有能力（新基因必须保留源基因的关键行为模式）",
    "不得从零重写（蒸馏只能增量总结共性，不能抛弃已验证的经验）",
    "不得添加与域无关的通用最佳实践",
    "不得改变基因的输入输出契约（system_prompt_addon 的注入语义不变）",
    "不得引入未经验证的外部依赖或新策略步骤",
    "每次蒸馏必须保留来源基因 id（changelog 溯源）",
    "新基因 confidence 不得超过源基因的合理上限（防冒进）",
]
# 蒸馏时新基因必须保留的源基因核心信号（差集比例上限）
CORE_SIGNAL_KEEP_RATIO = 0.6


def _violates_conservative_constraints(source_gene, new_signals):
    """保守编辑检查：新蒸馏基因不得破坏源基因核心能力。

    返回违规描述；通过返回 None。源基因缺失时视为通过（宽松）。
    """
    if not source_gene:
        return None
    src_signals = set(source_gene.get("trigger_signals") or [])
    if not src_signals:
        return None
    new_set = set(new_signals or [])
    kept = src_signals & new_set
    keep_ratio = len(kept) / len(src_signals)
    if keep_ratio < CORE_SIGNAL_KEEP_RATIO:
        return (f"核心信号保留率 {keep_ratio:.0%} 低于阈值 {CORE_SIGNAL_KEEP_RATIO:.0%}"
                f"（源 {sorted(src_signals)} → 新 {sorted(new_set)}）")
    return None


# ---------------- 技能蒸馏 ----------------

def distill_skill(domain):
    """技能蒸馏：7/10 成功 + 24h 间隔 + 保守编辑约束 → 生成新 Gene + 更新 L1

    [DISTILLED] (domain) → new_gene | None
    失败归因过滤：仅统计非 env_instability 的结果（环境异常不算基因成败），
    蒸馏目标基因需满足保守编辑约束，防止 LLM 过度改写。
    """
    recent = dbe.get_recent_capsules(domain, limit=10)
    if len(recent) < 10:
        return None

    # 仅把 gene_deficiency / 无失败归因的失败算作基因待改进项，环境异常不参与
    success_count = sum(1 for c in recent if c.get("user_adopted"))
    env_failures = sum(1 for c in recent
                       if not c.get("user_adopted") and c.get("failure_reason") == "env_instability")
    # 有效样本 = 总样本 - 环境异常（避免误判基因成功率偏低）
    effective_total = len(recent) - env_failures
    if effective_total < 7:
        return None
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

    # 保守编辑约束：源基因取最近成功胶囊对应的基因，校验新信号不破坏其核心能力
    source_gene = None
    for c in successful:
        sg = dbe.get_gene(c.get("gene_id"))
        if sg:
            source_gene = sg
            break
    violation = _violates_conservative_constraints(source_gene, common_signals)
    if violation:
        dbe.log_event("gene_distill_blocked", gene_id=source_gene["gene_id"] if source_gene else None,
                      domain=domain, details=f"conservative_constraint: {violation}")
        return None

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
                  details=(f"distilled from {success_count}/{len(recent)} capsules"
                           f" (env={env_failures} excluded, source={source_gene['gene_id'] if source_gene else '-'})"))

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
