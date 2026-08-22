"""架构落地路由：领域上下文 / 任务队列 / 单店档案 / 心跳复盘"""
from fastapi import APIRouter

import db
import heartbeat
from schemas import DomainContextIn, JobIn, StoreProfileIn

router = APIRouter(prefix="/api", tags=["arch"])

# -- 领域上下文 --
@router.get("/context")
def context_list(domain: str | None = None):
    return {"items": db.list_domain_context(domain)}

@router.get("/context/{domain}")
def context_get(domain: str):
    return {"items": db.list_domain_context(domain)}

@router.post("/context")
def context_set(data: DomainContextIn):
    return db.set_domain_context(data.domain, data.key or data.domain, data.value)

# -- 任务队列 --
@router.post("/jobs")
def job_enqueue(data: JobIn):
    return {"job_id": db.enqueue_job(data.task_type, data.payload), "status": "pending"}

@router.get("/queue")
def job_list(task_type: str | None = None, status: str | None = None, limit: int = 50):
    return {"items": db.list_jobs(task_type, status, limit)}

# -- 单店档案 --
@router.post("/store/profile")
def profile_save(data: StoreProfileIn, profile_id: int | None = None):
    pid = db.save_store_profile(
        data.name, profile_id=profile_id,
        biz_type=data.biz_type, gross_margin=data.gross_margin,
        rent=data.rent, salary=data.salary, utilities=data.utilities,
        total_investment=data.total_investment, cash_on_hand=data.cash_on_hand,
        traffic=data.traffic, competitor=data.competitor)
    return {"profile_id": pid}

@router.get("/profiles")
def profile_list():
    return {"items": db.list_store_profiles()}

@router.get("/profile/{profile_id}")
def profile_get(profile_id: int):
    p = db.load_store_profile(profile_id)
    return p if p else {"error": "档案不存在"}

@router.delete("/profile/{profile_id}")
def profile_delete(profile_id: int):
    return {"ok": db.delete_store_profile(profile_id)}

# -- 心跳复盘 --
@router.post("/heartbeat")
def heartbeat_generate():
    return {"ok": True, "review": heartbeat.generate_daily_review()}

@router.get("/heartbeat")
def heartbeat_read():
    text = heartbeat.daily_review_text()
    return {"ok": bool(text), "review": text}
