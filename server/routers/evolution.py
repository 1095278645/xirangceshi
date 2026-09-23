"""自适应进化层：只读摘要（含候选池 + 变更账本）+ 候选基因处理（验证/转正/归档）

批次 B 把它收敛为**内部离线机制**；批次 B+ 按业界共识补上三道护栏：
  - 变了要能验证：蒸馏产物先进 `candidate`，须过证据门（+可选基准门）才转 `active`
  - 改了要能审计：变更账本直接复用 `agent_events`（不新增表）
  - 升级要能回退：被否决/被抑制的基因保留为 `archived` / `suppressed`，不删除
"""
from fastapi import APIRouter, HTTPException

import config
import evolution_growth as growth
import team_domains
import team_evolution
from schemas import EvolutionCandidateIn

router = APIRouter(prefix="/api", tags=["evolution"])


@router.get("/evolution/summary")
def evolution_summary(domain: str = ""):
    """只读：启用状态 + 各域摘要 + 候选池 + 变更账本。"""
    base = {
        "enabled": config.evolution_enabled(),
        "benchmark_cmd_set": bool(config.EVOLUTION_VERIFY_CMD),
        "gate": {"min_adopted": config.EVOLUTION_VERIFY_MIN_ADOPTED,
                 "min_tasks": config.EVOLUTION_VERIFY_MIN_TASKS},
    }
    if domain:
        base.update({
            "domain": domain,
            "summary": team_evolution.get_evolution_summary(domain),
            "candidates": growth.list_candidates(domain),
            "ledger": growth.gene_ledger(domain),
        })
        return base
    base.update({
        "domains": {d: team_evolution.get_evolution_summary(d)
                    for d in team_domains.list_team_domains()},
        "candidates": growth.list_candidates(""),
        "ledger": growth.gene_ledger(""),
    })
    return base


@router.post("/evolution/candidates/{gene_id}")
def evolution_candidate_action(gene_id: str, data: EvolutionCandidateIn):
    """候选基因处理：`verify`（跑验证门）/ `approve`（人工转正，需 confirm）/ `reject`（归档）。"""
    action = (data.action or "verify").lower()
    if action == "verify":
        return growth.verify_candidate(gene_id)
    if action == "approve":
        result = growth.approve_candidate(gene_id, confirm=data.confirm)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "approve 失败"))
        return result
    if action == "reject":
        result = growth.reject_candidate(gene_id, reason=data.reason)
        if not result.get("ok"):
            raise HTTPException(404, result.get("error", "reject 失败"))
        return result
    raise HTTPException(400, f"未知 action：{action}（可用 verify / approve / reject）")
