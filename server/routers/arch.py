"""架构落地路由：领域上下文 / 任务队列 / 单店档案 / 心跳复盘

对标《一人公司设计哲学对标报告》技术架构三支柱：
  1. 领域上下文 = 每个业务域独立的经营记忆（workspace 隔离）
  2. 任务队列   = 心跳/日报等推送复用的可靠执行通道（重试+死信）
  3. 单店档案   = 单店经营引擎输入沉淀为可复用资产
  另：心跳复盘 = 主动生成掌柜每日复盘，落盘 domain_context(ledger, daily_review)
"""
from fastapi import APIRouter

import db
import heartbeat
import team_domains
import team_evolution
import evolution
import db_evolution as dbe
from schemas import (
    DomainContextIn, JobIn, StoreProfileIn,
    LearningIn, LearningQuery, OutcomeIn, GeneIn,
)

router = APIRouter(prefix="/api", tags=["arch"])


# ---------------- 领域上下文 ----------------
@router.get("/context")
def context_list(domain: str | None = None):
    """读取领域上下文；可只传 domain 过滤，不传返回全部"""
    return {"items": db.list_domain_context(domain)}


@router.get("/context/{domain}")
def context_get(domain: str):
    """读取某领域的全部上下文"""
    return {"items": db.list_domain_context(domain)}


@router.post("/context")
def context_set(data: DomainContextIn):
    """写入一条领域上下文（UPSERT）；key 空时默认用 domain 作单组织键"""
    key = data.key or data.domain
    return db.set_domain_context(data.domain, key, data.value)


# ---------------- 任务队列 ----------------
@router.post("/jobs")
def job_enqueue(data: JobIn):
    """入队一个任务，返回 job_id"""
    jid = db.enqueue_job(data.task_type, data.payload)
    return {"job_id": jid, "status": "pending"}


@router.get("/queue")
def job_list(task_type: str | None = None, status: str | None = None, limit: int = 50):
    """查询任务列表（可按 task_type / status 过滤）"""
    return {"items": db.list_jobs(task_type, status, limit)}


# ---------------- 单店档案 ----------------
@router.post("/store/profile")
def profile_save(data: StoreProfileIn, profile_id: int | None = None):
    """保存/更新一个店档案，返回档案 id"""
    pid = db.save_store_profile(
        data.name, profile_id=profile_id,
        biz_type=data.biz_type, gross_margin=data.gross_margin,
        rent=data.rent, salary=data.salary, utilities=data.utilities,
        total_investment=data.total_investment, cash_on_hand=data.cash_on_hand,
        traffic=data.traffic, competitor=data.competitor)
    return {"profile_id": pid}


@router.get("/profiles")
def profile_list():
    """列出全部店档案"""
    return {"items": db.list_store_profiles()}


@router.get("/profile/{profile_id}")
def profile_get(profile_id: int):
    """读取单个店档案（含上次诊断可复用输入）"""
    p = db.load_store_profile(profile_id)
    if not p:
        return {"error": "档案不存在"}
    return p


@router.delete("/profile/{profile_id}")
def profile_delete(profile_id: int):
    """删除一个店档案"""
    return {"ok": db.delete_store_profile(profile_id)}


# ---------------- 心跳复盘 ----------------
@router.post("/heartbeat")
def heartbeat_generate():
    """手动触发一次每日复盘，落盘 domain_context(ledger, daily_review)"""
    text = heartbeat.generate_daily_review()
    return {"ok": True, "review": text}


@router.get("/heartbeat")
def heartbeat_read():
    """读取最近一次落盘的今日复盘"""
    text = heartbeat.daily_review_text()
    return {"ok": bool(text), "review": text}


# ---------------- 自适应进化层 ----------------

@router.post("/learning")
def learning_record(data: LearningIn):
    """上报一条经验日志（前端检测到用户编辑/跳过/反馈时调用）"""
    lid = dbe.record_learning(
        domain=data.domain, trigger_type=data.trigger_type,
        pattern_key=data.pattern_key or None, source=data.source,
        details=data.details, metadata=data.metadata)
    return {"learning_id": lid}


@router.get("/learnings")
def learning_list(domain: str = "", status: str = "", limit: int = 100):
    """查询经验日志"""
    items = dbe.get_learnings(domain or None, status or None, limit)
    return {"items": items}


@router.post("/outcome")
def outcome_record(data: OutcomeIn):
    """记录用户行为结果（采纳/修改/跳过某条文案变体）"""
    cap_id = team_evolution.record_outcome(
        domain=data.domain, gene_id=data.gene_id, content=data.content,
        user_adopted=data.user_adopted, user_edited=data.user_edited,
        edit_diff=data.edit_diff or None, task_context=data.task_context)
    return {"capsule_id": cap_id}


@router.get("/genes")
def gene_list(domain: str = ""):
    """列出某域的全部基因（含 suppressed）"""
    items = dbe.get_all_genes(domain) if domain else []
    return {"items": items}


@router.post("/genes")
def gene_save(data: GeneIn):
    """创建或更新一个基因"""
    result = dbe.save_gene(
        gene_id=data.gene_id, domain=data.domain,
        trigger_signals=data.trigger_signals,
        system_prompt_addon=data.system_prompt_addon,
        strategy_steps=data.strategy_steps,
        category=data.category, is_distilled=data.is_distilled)
    dbe.log_event("gene_created", gene_id=data.gene_id, domain=data.domain,
                  details=f"manual gene creation")
    return result


@router.get("/capsules")
def capsule_list(domain: str = "", limit: int = 20):
    """列出某域最近的胶囊记录"""
    items = dbe.get_recent_capsules(domain, limit) if domain else []
    return {"items": items}


@router.get("/events")
def event_list(domain: str = "", event_type: str = "", limit: int = 50):
    """查询审计事件（append-only）"""
    items = dbe.get_events(domain or None, event_type or None, limit)
    return {"items": items}


@router.get("/evolution/{domain}")
def evolution_summary(domain: str):
    """获取某域的进化状态摘要"""
    return team_evolution.get_evolution_summary(domain)


@router.post("/evolution/seed")
def evolution_seed():
    """初始化基因库（幂等：已存在的基因不覆盖统计）"""
    count = team_evolution.seed_initial_genes()
    return {"seeded": count}


@router.post("/evolution/check")
def evolution_check():
    """手动触发每日进化检查（晋升/抑制/蒸馏）"""
    results = heartbeat.evolution_daily_check()
    return {"ok": True, "results": results}