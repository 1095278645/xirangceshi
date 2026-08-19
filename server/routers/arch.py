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
from schemas import DomainContextIn, JobIn, StoreProfileIn

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