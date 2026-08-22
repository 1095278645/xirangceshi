"""evolution.py — 自适应进化引擎

四层架构的算法层（对标三项目源码核心机制）：
  select_gene      — 基因选择：Laplace 平滑 + 时间衰减 + 遗传漂移（EvoMap/evolver）
  suppress_gene    — 基因抑制：低成功率/长连败 → suppressed（EvoMap/evolver）
  auto_strategy    — 策略自动选择：根据近期失败率选预设（EvoMap/evolver）
  distill_skill    — 技能蒸馏：7/10 成功 + 24h 间隔 → 新 Gene + L1 更新（GenericAgent）
  promote_learning — 经验晋升：3 次 + 2 任务 + 30 天 → domain_context（self-improving-agent）
  review_injection — 任务前回顾：注入 pending 条目到提示词（self-improving-agent）
  record_outcome   — 评估记录：用户采纳/修改/跳过 → Capsule + Event + 基因统计更新

设计约束：
  - 纯本地算法，不依赖 AI API
  - 无 API Key 时进化层不启动（由 team_domains.py 调用方判断）
  - 所有进化操作记入 append-only 审计日志
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

import db_evolution as dbe
import team_evolution as te
from db_arch import set_domain_context, get_domain_context

# 晋升规则阈值（self-improving-agent）
PROMOTE_RECURRENCE = 3       # 复现次数 >= 3
PROMOTE_DISTINCT_TASKS = 2  # 跨至少 2 个不同任务
PROMOTE_DAYS_WINDOW = 30    # 30 天窗口内

# 基因抑制阈值（EvoMap/evolver）
SUPPRESS_MIN_ATTEMPTS = 4    # 尝试 >= 4 次
SUPPRESS_MAX_SUCCESS_RATE = 0.15  # 成功率 <= 15%
SUPPRESS_CONSECUTIVE_INERT = 8   # 连续 8 次零工作结果

# 技能蒸馏阈值（GenericAgent）
DISTILL_SUCCESS_COUNT = 7    # 最近 10 个胶囊中 >= 7 个成功
DISTILL_HOURS_GAP = 24        # 距上次蒸馏 >= 24 小时
DISTILL_SCORE_MULTIPLIER = 0.8  # 蒸馏基因 confidence 乘 0.8


# ---------------- 基因选择（7 阶段 · 阶段 2） ----------------

def select_gene(domain, signals, strategy="auto"):
    """基因选择：信号匹配 → Laplace 排序 → 策略过滤 → 遗传漂移 → 抑制检查

    返回选中的基因 dict 或 None（无基因时）。
    """
    # 输入护栏
    if not domain or not isinstance(domain, str):
        return None
    if not isinstance(signals, list):
        signals = list(signals) if signals else []

    genes = dbe.get_active_genes(domain)
    if not genes:
        return None

    # 1. 信号匹配：过滤触发信号匹配的基因
    matched = [g for g in genes
               if any(s in g.get("trigger_signals", []) for s in signals)]
    if not matched:
        matched = genes  # 无匹配则全部候选

    # 2. Laplace 平滑成功率 + 时间衰减
    for g in matched:
        total = g.get("success_count", 0) + g.get("failure_count", 0)
        p = (g.get("success_count", 0) + 1) / (total + 2)
        age_days = _age_days(g.get("last_used") or g.get("created"))
        weight = 0.5 ** (age_days / 30)
        g["score"] = p * weight

    # 3. 策略过滤
    if strategy == "auto":
        strategy = auto_strategy(domain)

    if strategy == "repair_only":
        matched = [g for g in matched if g.get("category") == "repair"]
        if not matched:
            matched = genes  # 无 repair 类基因则回退
    elif strategy == "innovate":
        matched = sorted(matched, key=lambda g: g["score"], reverse=True)
        matched = matched[:max(1, len(matched) * 3 // 4)]  # 保留 75%

    if not matched:
        return None

    # 4. 遗传漂移：概率 = 1/sqrt(gene_count)
    gene_count = len(genes)
    if random.random() < 1 / math.sqrt(gene_count):
        selected = random.choice(matched)  # 探索
    else:
        selected = max(matched, key=lambda g: g["score"])  # 利用

    # 5. 抑制检查
    if selected.get("status") == "suppressed":
        active = [g for g in matched if g.get("status") == "active"]
        if active:
            selected = max(active, key=lambda g: g["score"])

    return selected


# ---------------- 策略自动选择（阶段 2 辅助） ----------------

def auto_strategy(domain):
    """根据近期失败率自动选择策略"""
    recent = dbe.get_recent_capsules(domain, limit=20)
    if not recent:
        return "balanced"
    failure_rate = sum(1 for c in recent if not c.get("user_adopted")) / len(recent)
    if failure_rate >= 0.4:
        return "harden"
    if failure_rate >= 0.3:
        return "balanced"
    if failure_rate <= 0.1:
        return "innovate"
    return "balanced"


# ---------------- 基因抑制（阶段 6 辅助） ----------------

def suppress_gene(gene_id):
    """基因抑制：检查条件并标记 suppressed，记审计事件"""
    gene = dbe.get_gene(gene_id)
    if not gene:
        return False

    total = gene.get("success_count", 0) + gene.get("failure_count", 0)
    success_rate = (gene.get("success_count", 0) + 1) / (total + 2) if total > 0 else 0.5

    should_suppress = False
    reason = ""

    # 条件 1: >= 4 次尝试且成功率 <= 15%
    if total >= SUPPRESS_MIN_ATTEMPTS and success_rate <= SUPPRESS_MAX_SUCCESS_RATE:
        should_suppress = True
        reason = f"low_success_rate: {total} attempts, rate={success_rate:.2f}"

    # 条件 2: 连续 8 次零工作结果
    if gene.get("consecutive_inert", 0) >= SUPPRESS_CONSECUTIVE_INERT:
        should_suppress = True
        reason = f"consecutive_inert: {gene['consecutive_inert']}"

    if should_suppress:
        dbe.set_gene_status(gene_id, "suppressed")
        dbe.log_event("gene_suppressed", gene_id=gene_id, domain=gene.get("domain"),
                       details=reason)
        return True
    return False


def check_and_suppress(domain):
    """检查某域所有 active 基因，对满足抑制条件的执行抑制"""
    genes = dbe.get_all_genes(domain)
    suppressed = []
    for g in genes:
        if g.get("status") == "active":
            if suppress_gene(g["gene_id"]):
                suppressed.append(g["gene_id"])
    return suppressed


# ---------------- 技能蒸馏（阶段 7 固化） ----------------

def distill_skill(domain):
    """技能蒸馏：7/10 成功 + 24h 间隔 → 生成新 Gene + 更新 L1

    返回新基因 dict 或 None（不满足条件时）。
    """
    recent = dbe.get_recent_capsules(domain, limit=10)
    if len(recent) < 10:
        return None

    success_count = sum(1 for c in recent if c.get("user_adopted"))
    if success_count < DISTILL_SUCCESS_COUNT:
        return None

    # 检查距上次蒸馏的时间间隔
    last_distill = get_domain_context(domain, "last_distill_time")
    if last_distill and last_distill.get("value"):
        try:
            last_dt = datetime.fromisoformat(last_distill["value"])
            hours_gap = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if hours_gap < DISTILL_HOURS_GAP:
                return None
        except (ValueError, TypeError):
            pass  # 解析失败则跳过间隔检查

    # 提取共性策略：从成功胶囊中提取共同特征
    successful = [c for c in recent if c.get("user_adopted")]
    common_signals = te.extract_common_signals(successful)
    common_content_pattern = te.extract_content_pattern(successful)

    # 生成新基因
    gene_id = f"gene_distilled_{domain}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    new_gene = dbe.save_gene(
        gene_id=gene_id,
        domain=domain,
        trigger_signals=common_signals,
        system_prompt_addon=common_content_pattern,
        strategy_steps=None,
        confidence=0.5 * DISTILL_SCORE_MULTIPLIER,  # 蒸馏基因略低
        success_count=0,
        failure_count=0,
        status="active",
        category="reinforce",
        is_distilled=1,
    )

    # 记录审计事件
    dbe.log_event("gene_distilled", gene_id=gene_id, domain=domain,
                  details=f"distilled from {success_count}/10 successful capsules")

    # 更新 L1 索引
    te.update_insight_index(domain)

    # 更新蒸馏时间
    set_domain_context(domain, "last_distill_time",
                       datetime.now(timezone.utc).isoformat())

    return new_gene


# ---------------- 经验晋升（Layer 1 → Layer 2） ----------------

def promote_learning(domain):
    """检查某域的经验日志，满足晋升条件的提升到 domain_context

    晋升条件：recurrence_count >= 3 AND distinct_tasks >= 2 AND days_since_first <= 30
    返回晋升的条目列表。
    """
    pending = dbe.get_pending_learnings(domain)
    promoted = []

    for item in pending:
        if item.get("recurrence_count", 0) < PROMOTE_RECURRENCE:
            continue

        # distinct_tasks：从 metadata 中提取不同任务标识
        metadata = item.get("metadata") or {}
        distinct_tasks = metadata.get("distinct_tasks", 1)
        if isinstance(distinct_tasks, list):
            distinct_count = len(distinct_tasks)
        else:
            distinct_count = int(distinct_tasks)

        if distinct_count < PROMOTE_DISTINCT_TASKS:
            continue

        # 时间窗口检查
        first_seen = item.get("first_seen", "")
        if first_seen:
            try:
                first_dt = datetime.fromisoformat(first_seen)
                days_since = (datetime.now(timezone.utc) - first_dt).days
                if days_since > PROMOTE_DAYS_WINDOW:
                    continue
            except (ValueError, TypeError):
                pass  # 解析失败则跳过窗口检查

        # 晋升：写入 domain_context 作为永久记忆
        key = f"promoted_{item['pattern_key'] or item['id']}"
        summary = (f"[晋升自经验日志] {item.get('pattern_key', '')}: "
                   f"{item.get('details', '')[:200]}")
        set_domain_context(domain, key, summary)
        dbe.promote_learning(item["id"])
        dbe.log_event("learning_promoted", domain=domain,
                      details=f"pattern={item.get('pattern_key')}, count={item['recurrence_count']}")
        promoted.append(item)

    # 晋升后更新 L1 索引
    if promoted:
        te.update_insight_index(domain)

    return promoted


# ---------------- 任务前回顾（Layer 1 → 提示词注入） ----------------

def review_injection(domain):
    """获取某域 pending 的经验条目，格式化为提示词注入文本

    返回注入文本（空字符串表示无待回顾条目）。
    """
    pending = dbe.get_pending_learnings(domain)
    if not pending:
        return ""

    lines = []
    for item in pending[:5]:  # 最多注入 5 条
        pk = item.get("pattern_key", "")
        details = item.get("details", "")[:100]
        count = item.get("recurrence_count", 1)
        lines.append(f"· {pk}（出现{count}次）：{details}")

    return ("【历史经验提醒】以下是该域近期出现的问题模式，请避免：\n"
            + "\n".join(lines))


# ---------------- 评估记录（阶段 6 评估） ----------------

def record_outcome(domain, gene_id, content, user_adopted=False,
                   user_edited=False, edit_diff=None, task_context=None):
    """记录用户行为结果到 Capsule + Event，并更新基因统计

    返回 capsule_id。
    """
    capsule_id = f"cap-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"

    gene = dbe.get_gene(gene_id)
    confidence = gene.get("confidence", 0.5) if gene else 0.5

    # 保存胶囊
    dbe.save_capsule(
        capsule_id=capsule_id,
        gene_id=gene_id,
        domain=domain,
        task_context=task_context,
        content=content,
        user_adopted=user_adopted,
        user_edited=user_edited,
        edit_diff=edit_diff,
        confidence=confidence,
    )

    # 记录审计事件
    event_type = "capsule_created"
    dbe.log_event(event_type, gene_id=gene_id, capsule_id=capsule_id,
                  domain=domain,
                  details=f"adopted={user_adopted}, edited={user_edited}",
                  content=content)

    # 更新基因统计
    if user_adopted:
        dbe.update_gene_stats(gene_id, success=True)
    else:
        dbe.update_gene_stats(gene_id, failure=True, inert=not user_edited)

    # 检查是否需要抑制
    suppress_gene(gene_id)

    return capsule_id


# ---------------- 辅助函数 ----------------

def _age_days(iso_str):
    """计算 ISO 时间戳距今的天数"""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except (ValueError, TypeError):
        return 0
