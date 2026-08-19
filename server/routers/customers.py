"""熟客 / 记忆 / 提醒"""
from fastapi import APIRouter, HTTPException

import ai
import db
from schemas import CustomerIn, MemoryIn

router = APIRouter(prefix="/api", tags=["customers"])


@router.get("/customers")
def customers():
    return db.list_customers()


@router.get("/customers/{cid}")
def customer_detail(cid: int):
    c = db.get_customer(cid)
    if not c:
        raise HTTPException(404, "客户不存在")
    return c


@router.post("/customers")
def create_customer(data: CustomerIn):
    cid, is_new = db.find_or_create_customer(data.name, data.phone, data.tags, data.favorite)
    return {"customer_id": cid, "is_new": is_new}


@router.post("/memories")
def add_memory(data: MemoryIn):
    db.add_memory(data.customer_id, data.content)
    return {"ok": True}


@router.post("/reminders/generate")
def reminders_generate():
    """用 AI 生成今日提醒并入库"""
    customers = db.list_customers()
    if not customers:
        return {"reminders": []}
    mem_map = db.recent_memories(3)          # 单次查询取全部熟客近期记忆，避免 N+1
    brief = "\n".join(
        f"{c['name']}（常点：{c['favorite'] or '未知'}）"
        + (f"，最近记忆：{'；'.join(mem_map.get(c['id'], []))}" if mem_map.get(c['id']) else "")
        for c in customers[:20])
    items = ai.generate_reminders(brief)
    saved = []
    for it in items:
        cid, _ = db.find_or_create_customer(it.get("customer", ""))
        rid = db.add_reminder(cid, it.get("content", ""))
        saved.append({"id": rid, "customer": it.get("customer"), "content": it.get("content")})
    return {"reminders": saved}


@router.get("/reminders")
def reminders_list(done: int | None = None):
    return db.list_reminders(done)


@router.post("/reminders/{rid}/done")
def reminder_done(rid: int, done: int = 1):
    db.mark_reminder_done(rid, done)
    return {"ok": True}


@router.post("/customers/{cid}/insight")
def customer_insight(cid: int):
    """AI 客户画像：读取熟客交易+记忆 → 读取上次画像(domain_context) → AI 生成 → 落盘"""
    c = db.get_customer(cid)
    if not c:
        raise HTTPException(404, "客户不存在")
    txns = c.get("transactions", [])
    prev = db.get_domain_context("customer", f"profile_{cid}")
    prev_text = prev["value"] if prev else ""
    text = ai.generate_customer_insight(c, txns)
    db.set_domain_context("customer", f"profile_{cid}", text)
    return {"insight": text, "customer": c, "ai_used": ai.ai_available()}