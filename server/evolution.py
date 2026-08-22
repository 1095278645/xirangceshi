"""evolution.py — 自适应进化引擎（核心：基因选择 + 策略）

状态机（L6 转换管线，显式状态 + 转换契约 + 终止条件）：

  [INPUT] domain + signals
    → [GENE_SELECTED]  select_gene(domain, signals) → gene | None
    → [CONTENT_GENERATED]  team_domains._run_team(gene) → text
    → [OUTCOME_RECORDED]  record_outcome(text, adopted) → capsule_id
    → [DAILY_CHECK]  heartbeat.evolution_daily_check()
        → [SUPPRESSED]  suppress_gene(gene_id) → bool
        → [DISTILLED]   distill_skill(domain) → gene | None
        → [PROMOTED]    promote_learning(domain) → [items]
    → [OUTPUT]  get_evolution_summary(domain) → dict

  终止条件：每日检查完成（所有注册域遍历完毕）。
  幂等性：seed 幂等；distill 24h 间隔；promote 后 status 不可逆。

拆分说明：抑制/评估 → evolution_lifecycle.py；蒸馏/晋升/回顾 → evolution_growth.py。
向后兼容：本文件 re-export 全部拆出函数，调用方仍可 evolution.record_outcome 等。
"""
import math
import random

import db_evolution as dbe

# 向后兼容 re-export
from evolution_lifecycle import (  # noqa: F401
    suppress_gene, check_and_suppress, record_outcome,
    SUPPRESS_MIN_ATTEMPTS, SUPPRESS_MAX_SUCCESS_RATE, SUPPRESS_CONSECUTIVE_INERT,
)
from evolution_growth import (  # noqa: F401
    distill_skill, promote_learning, review_injection,
    DISTILL_SUCCESS_COUNT, DISTILL_HOURS_GAP, DISTILL_SCORE_MULTIPLIER,
    PROMOTE_RECURRENCE, PROMOTE_DISTINCT_TASKS, PROMOTE_DAYS_WINDOW,
)


# ---------------- 基因选择（7 阶段 · 阶段 2） ----------------

def select_gene(domain, signals, strategy="auto"):
    """[INPUT → GENE_SELECTED] 基因选择：信号匹配 → Laplace 排序 → 策略过滤 → 遗传漂移 → 抑制检查

    返回选中的基因 dict 或 None（无基因时）。
    """
    if not domain or not isinstance(domain, str):
        return None
    if not isinstance(signals, list):
        signals = list(signals) if signals else []

    genes = dbe.get_active_genes(domain)
    if not genes:
        return None

    # 1. 信号匹配
    matched = [g for g in genes
               if any(s in g.get("trigger_signals", []) for s in signals)]
    if not matched:
        matched = genes

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
            matched = genes
    elif strategy == "innovate":
        matched = sorted(matched, key=lambda g: g["score"], reverse=True)
        matched = matched[:max(1, len(matched) * 3 // 4)]
    elif strategy == "harden":
        # 保守：只取得分靠前的一半，且不做探索性漂移（避免冒进）
        matched = sorted(matched, key=lambda g: g["score"], reverse=True)
        matched = matched[:max(1, len(matched) // 2)]

    if not matched:
        return None

    # 4. 遗传漂移：概率 = 1/sqrt(gene_count)；harden 保守策略不漂移（不随机探索）
    gene_count = len(genes)
    if strategy != "harden" and random.random() < 1 / math.sqrt(gene_count):
        selected = random.choice(matched)
    else:
        selected = max(matched, key=lambda g: g["score"])

    # 5. 抑制检查
    if selected.get("status") == "suppressed":
        active = [g for g in matched if g.get("status") == "active"]
        if active:
            selected = max(active, key=lambda g: g["score"])

    return selected


# ---------------- 策略自动选择 ----------------

def auto_strategy(domain):
    """[辅助] 根据近期失败率自动选择策略"""
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


# ---------------- 辅助函数 ----------------

def _age_days(iso_str):
    """计算 ISO 时间戳距今的天数"""
    if not iso_str:
        return 0
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso_str)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except (ValueError, TypeError):
        return 0
