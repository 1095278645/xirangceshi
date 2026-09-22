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
    """立即让掌柜复盘一次（多 agent：五位伙计各管一摊 → 掌柜裁决）。

    耗时约 5~10 秒（6 次模型调用），前端要给出等待提示。
    """
    text = heartbeat.generate_daily_review()
    return {"ok": True, "review": text,
            "layers": heartbeat.daily_review_layers(),
            "snapshot": heartbeat.daily_snapshot_text()}


@router.get("/heartbeat")
def heartbeat_read():
    text = heartbeat.daily_review_text()
    return {"ok": bool(text), "review": text,
            "layers": heartbeat.daily_review_layers(),
            "snapshot": heartbeat.daily_snapshot_text()} if text else {
        "ok": False, "review": None, "layers": None, "snapshot": None}


@router.get("/heartbeat/snapshot")
def heartbeat_snapshot():
    """掌柜看到的全店事实（原始快照，不含 AI 加工）。

    为什么要单独暴露：复盘是**取舍后**的结论，"为什么这么说"要能查证。
    这块也能让店主看到店里哪些经营动作还没记账（库存、发票、赊账…）。
    """
    import shop_snapshot
    live = shop_snapshot.build_snapshot()
    return {"ok": True, "snapshot": live,
            "cached": heartbeat.daily_snapshot_text(),
            "facts": shop_snapshot.snapshot_facts()}
