"""自适应进化层路由：经验日志 / 基因库 / 胶囊 / 事件 / 进化检查"""
from fastapi import APIRouter

import heartbeat
import team_evolution
import db_evolution as dbe
from schemas import LearningIn, OutcomeIn, GeneIn

router = APIRouter(prefix="/api", tags=["evolution"])

@router.post("/learning")
def learning_record(data: LearningIn):
    lid = dbe.record_learning(
        domain=data.domain, trigger_type=data.trigger_type,
        pattern_key=data.pattern_key or None, source=data.source,
        details=data.details, metadata=data.metadata)
    return {"learning_id": lid}

@router.get("/learnings")
def learning_list(domain: str = "", status: str = "", limit: int = 100):
    return {"items": dbe.get_learnings(domain or None, status or None, limit)}

@router.post("/outcome")
def outcome_record(data: OutcomeIn):
    cap_id = team_evolution.record_outcome(
        domain=data.domain, gene_id=data.gene_id, content=data.content,
        user_adopted=data.user_adopted, user_edited=data.user_edited,
        edit_diff=data.edit_diff or None, task_context=data.task_context)
    # 经验采集：用户采纳前做了修改 → 记一条"需改进"的经验模式（去重累加复现次数）
    if data.user_edited:
        dbe.record_learning(
            data.domain, "user_edited", f"{data.domain}.user-edited",
            source="frontend", details="用户对AI产出做了修改后采纳/放弃",
            metadata={"distinct_tasks": [str(data.task_context or "")]})
    return {"capsule_id": cap_id}

@router.get("/genes")
def gene_list(domain: str = ""):
    return {"items": dbe.get_all_genes(domain) if domain else []}

@router.post("/genes")
def gene_save(data: GeneIn):
    result = dbe.save_gene(
        gene_id=data.gene_id, domain=data.domain,
        trigger_signals=data.trigger_signals,
        system_prompt_addon=data.system_prompt_addon,
        strategy_steps=data.strategy_steps,
        category=data.category, is_distilled=data.is_distilled)
    dbe.log_event("gene_created", gene_id=data.gene_id, domain=data.domain,
                  details="manual gene creation")
    return result

@router.get("/capsules")
def capsule_list(domain: str = "", limit: int = 20):
    return {"items": dbe.get_recent_capsules(domain, limit) if domain else []}

@router.get("/events")
def event_list(domain: str = "", event_type: str = "", limit: int = 50):
    return {"items": dbe.get_events(domain or None, event_type or None, limit)}

@router.get("/evolution/{domain}")
def evolution_summary(domain: str):
    return team_evolution.get_evolution_summary(domain)

@router.post("/evolution/seed")
def evolution_seed():
    return {"seeded": team_evolution.seed_initial_genes()}

@router.post("/evolution/check")
def evolution_check():
    return {"ok": True, "results": heartbeat.evolution_daily_check()}
