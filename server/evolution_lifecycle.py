"""evolution_lifecycle.py — 基因抑制 + 评估记录（从 evolution.py 拆出）

职责：基因抑制检查 + 用户行为结果记录到 Capsule/Event/基因统计。
依赖方向：evolution_lifecycle → db_evolution（无循环依赖）。
"""
import random
from datetime import datetime

import db_evolution as dbe

# 基因抑制阈值（EvoMap/evolver）
SUPPRESS_MIN_ATTEMPTS = 4
SUPPRESS_MAX_SUCCESS_RATE = 0.15
SUPPRESS_CONSECUTIVE_INERT = 8

# 失败原因分类（借鉴 SkillClaw 的三类问题区分）
REASON_GENE = "gene_deficiency"   # 基因本身不足 → 触发蒸馏
REASON_AGENT = "agent_runtime"    # Agent 推理不足 → 非基因问题，不蒸馏
REASON_ENV = "env_instability"    # 环境不稳定（超时/工具失败）→ 不蒸馏
FAILURE_REASONS = {REASON_GENE, REASON_AGENT, REASON_ENV}


def classify_failure(user_adopted=False, user_edited=False, env_error=False):
    """将未采纳结果归因到三类失败原因之一（SkillClaw 问题区分）。

    采纳/用户主动编辑 → 无失败原因（None）。
    环境异常（如 API 超时/工具失败）→ env_instability。
    否则默认基因缺陷 gene_deficiency（仅此触发蒸馏）。
    """
    if user_adopted or user_edited:
        return None
    if env_error:
        return REASON_ENV
    return REASON_GENE


# ---------------- 基因抑制 ----------------

def suppress_gene(gene_id):
    """基因抑制：检查条件并标记 suppressed，记审计事件"""
    gene = dbe.get_gene(gene_id)
    if not gene:
        return False

    total = gene.get("success_count", 0) + gene.get("failure_count", 0)
    success_rate = (gene.get("success_count", 0) + 1) / (total + 2) if total > 0 else 0.5

    should_suppress = False
    reason = ""

    if total >= SUPPRESS_MIN_ATTEMPTS and success_rate <= SUPPRESS_MAX_SUCCESS_RATE:
        should_suppress = True
        reason = f"low_success_rate: {total} attempts, rate={success_rate:.2f}"

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


# ---------------- 评估记录 ----------------

def record_outcome(domain, gene_id, content, user_adopted=False,
                   user_edited=False, edit_diff=None, task_context=None,
                   failure_reason=None, env_error=False):
    """记录用户行为结果到 Capsule + Event，并更新基因统计

    [OUTCOME_RECORDED] (domain, gene_id, content, adopted) → capsule_id
    failure_reason：未采纳时的失败归因；不传时用 classify_failure 自动归类。
    env_error=True 表示环境异常（API超时/工具失败），归类为 env_instability。
    """
    capsule_id = f"cap-{datetime.now().strftime('%Y%m%d%H%M%S')}-{random.randint(1000, 9999)}"

    gene = dbe.get_gene(gene_id)
    confidence = gene.get("confidence", 0.5) if gene else 0.5

    # 失败归因：显式传入优先，否则自动分类
    if failure_reason is None and not user_adopted:
        failure_reason = classify_failure(user_adopted, user_edited, env_error)
    if failure_reason not in FAILURE_REASONS:
        failure_reason = None

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
        failure_reason=failure_reason,
    )

    dbe.log_event("capsule_created", gene_id=gene_id, capsule_id=capsule_id,
                  domain=domain,
                  details=f"adopted={user_adopted}, edited={user_edited}, reason={failure_reason or '-'}",
                  content=content)

    if user_adopted:
        dbe.update_gene_stats(gene_id, success=True)
    else:
        dbe.update_gene_stats(gene_id, failure=True, inert=not user_edited)

    suppress_gene(gene_id)
    return capsule_id
