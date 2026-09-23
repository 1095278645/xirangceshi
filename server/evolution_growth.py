"""evolution_growth.py — 技能蒸馏 + 经验晋升 + 任务前回顾（从 evolution.py 拆出）

职责：从历史执行中提炼新基因、晋升经验到永久记忆、任务前注入经验提醒。
依赖方向：evolution_growth → db_evolution + team_evolution（无循环依赖）。
"""
from datetime import datetime, timezone

import config
import db_evolution as dbe
import team_evolution as te
from db_arch import set_domain_context, get_domain_context

# 技能蒸馏阈值（来自 config，便于统一调整；批次 B 收敛后不再散落魔法数）
DISTILL_SUCCESS_COUNT = config.EVOLUTION_DISTILL_SUCCESS_COUNT
DISTILL_HOURS_GAP = config.EVOLUTION_DISTILL_HOURS_GAP
DISTILL_SCORE_MULTIPLIER = 0.8

# 晋升规则阈值（self-improving-agent）
PROMOTE_RECURRENCE = config.EVOLUTION_PROMOTE_RECURRENCE
PROMOTE_DISTINCT_TASKS = config.EVOLUTION_PROMOTE_DISTINCT_TASKS
PROMOTE_DAYS_WINDOW = config.EVOLUTION_PROMOTE_DAYS_WINDOW

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
    # 最小样本量保护：样本不足时不做蒸馏（调用时读 config，便于测试覆盖）
    if effective_total < config.EVOLUTION_MIN_SAMPLES:
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
        # 批次 B+：蒸馏产物先进"候选池"，必须过验证门（或人工确认）才转 active。
        # 对应业界做法：DGM 的"改动必须实证验证"、Hermes 的"约束门禁"。
        status="candidate",
        category="reinforce",
        is_distilled=1,
    )

    dbe.log_event("gene_distilled", gene_id=gene_id, domain=domain,
                  details=(f"distilled from {success_count}/{len(recent)} capsules"
                           f" (env={env_failures} excluded, source={source_gene['gene_id'] if source_gene else '-'})"
                           f"；status=candidate（待验证，见 verify_candidate）"))

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

    # 注：晋升的最小证据由"复现次数 ≥3 + 不同任务 ≥2"自身保证（见下方条件），
    # 不再叠加胶囊总样本门槛——两者证据类型不同，混用会误伤。
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


# ---------------- 候选池 + 验证门 + 人工确认 + 归档（批次 B+） ----------------
#
# 业界共识（见 deliverables/GitHub自进化项目学习报告.md）：
#   变了要能验证、改了要能审计、升级要能回退。
# 因此蒸馏/晋升产物一律先进 candidate；只有"过门"（证据门 + 可选外部基准门）
# 或人工 confirm 才转 active；被否决的进 archived（保留可查，不删除）。

import json as _json
import subprocess as _subprocess


def _adoption_evidence(domain: str) -> dict:
    """从近期胶囊提取证据：采纳次数 + 覆盖的不同任务数。

    注意：只认 **user_adopted**（真实信号）。模型裁决计数不算证据 —— 否则就是我们
    在报告里批评过的"模型自证环路"。
    """
    capsules = dbe.get_recent_capsules(domain, limit=200) or []
    adopted = [c for c in capsules if c.get("user_adopted")]
    tasks = set()
    for c in adopted:
        raw = c.get("task_context")
        if not raw:
            continue
        try:
            ctx = _json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        key = str(ctx)[:120] if not isinstance(ctx, dict) else str(
            sorted(ctx.items()))[:120]
        if key:
            tasks.add(key)
    return {"captures": len(capsules), "adopted": len(adopted),
            "distinct_tasks": len(tasks)}


def _run_external_benchmark() -> dict:
    """可选外部基准门：执行 config.EVOLUTION_VERIFY_CMD，退出码 0 才算通过。"""
    cmd = (config.EVOLUTION_VERIFY_CMD or "").strip()
    if not cmd:
        return {"configured": False, "passed": True}
    try:
        proc = _subprocess.run(cmd, shell=True, capture_output=True,
                               timeout=config.EVOLUTION_VERIFY_TIMEOUT)
        tail = (proc.stdout or b"").decode("utf-8", "ignore")[-400:]
        return {"configured": True, "passed": proc.returncode == 0,
                "returncode": proc.returncode, "tail": tail}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "passed": False, "error": str(e)}


def list_candidates(domain: str = "") -> list:
    """列出候选基因（status=candidate）。未传域时遍历所有已注册域。"""
    domains = [domain] if domain else list(_team_domains().list_team_domains())
    out = []
    for d in domains:
        out.extend(g for g in dbe.get_all_genes(d) if g.get("status") == "candidate")
    return out


def _team_domains():
    """延迟导入 team_domains（避免与其形成顶层导入环）。"""
    import team_domains
    return team_domains


def verify_candidate(gene_id: str, domain: str = "") -> dict:
    """验证门：候选基因是否够格转正。

    - 证据门：该域**真实采纳**次数 ≥ EVOLUTION_VERIFY_MIN_ADOPTED 且覆盖任务 ≥ MIN_TASKS；
    - 基准门（可选）：若配置了 EVOLUTION_VERIFY_CMD，需其退出码为 0；
    通过 → status=active（+ 审计事件）；不通过 → 保持 candidate（+ 审计事件）。
    """
    gene = dbe.get_gene(gene_id)
    if not gene:
        return {"ok": False, "error": "基因不存在"}
    if gene.get("status") != "candidate":
        return {"ok": False, "error": f"仅候选基因可验证（当前 {gene.get('status')}）"}
    dom = domain or gene.get("domain") or ""
    ev = _adoption_evidence(dom)
    gate_ok = (ev["adopted"] >= config.EVOLUTION_VERIFY_MIN_ADOPTED
               and ev["distinct_tasks"] >= config.EVOLUTION_VERIFY_MIN_TASKS)
    bench = _run_external_benchmark()
    ok = gate_ok and bench.get("passed", True)

    if ok:
        dbe.set_gene_status(gene_id, "active")
        dbe.log_event("gene_validated", gene_id=gene_id, domain=dom,
                      details=(f"证据：adopted={ev['adopted']}/任务={ev['distinct_tasks']}；"
                               f"基准={'通过' if bench.get('configured') else '未配置'}"))
    else:
        dbe.log_event("gene_validation_failed", gene_id=gene_id, domain=dom,
                      details=(f"证据：adopted={ev['adopted']}/任务={ev['distinct_tasks']}；"
                               f"需 adopted≥{config.EVOLUTION_VERIFY_MIN_ADOPTED} 且 任务≥"
                               f"{config.EVOLUTION_VERIFY_MIN_TASKS}；基准={bench}"))
    return {"ok": ok, "gene_id": gene_id, "evidence": ev, "benchmark": bench,
            "gate": {"min_adopted": config.EVOLUTION_VERIFY_MIN_ADOPTED,
                     "min_tasks": config.EVOLUTION_VERIFY_MIN_TASKS}}


def approve_candidate(gene_id: str, confirm: bool = False) -> dict:
    """人工确认转正：必须 confirm=True（与项目其它危险操作一致，防误触）。"""
    gene = dbe.get_gene(gene_id)
    if not gene:
        return {"ok": False, "error": "基因不存在"}
    if not confirm:
        return {"ok": False, "error": "转正需 confirm=true"}
    dbe.set_gene_status(gene_id, "active")
    dbe.log_event("gene_approved", gene_id=gene_id, domain=gene.get("domain"),
                  details="人工确认转正（跳过自动验证门）")
    return {"ok": True, "gene_id": gene_id, "status": "active"}


def reject_candidate(gene_id: str, reason: str = "") -> dict:
    """否决候选：进 archived（保留可查、不再使用），而非删除。"""
    gene = dbe.get_gene(gene_id)
    if not gene:
        return {"ok": False, "error": "基因不存在"}
    dbe.set_gene_status(gene_id, "archived")
    dbe.log_event("gene_rejected", gene_id=gene_id, domain=gene.get("domain"),
                  details=reason or "人工否决（归档保留）")
    return {"ok": True, "gene_id": gene_id, "status": "archived"}


def gene_ledger(domain: str = "", limit: int = 50) -> list:
    """可读的"基因变更账本"：直接复用 agent_events（不新增表）。"""
    types = ("gene_distilled", "gene_validated", "gene_validation_failed",
             "gene_approved", "gene_rejected", "gene_suppressed", "gene_created")
    events = dbe.get_events(domain or None, None, limit * 3) or []
    return [e for e in events if e.get("event_type") in types][:limit]
