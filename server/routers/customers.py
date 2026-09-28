"""熟客 / 记忆 / 提醒"""
from fastapi import APIRouter, BackgroundTasks, HTTPException

import ai
import db
import notifications
from schemas import CustomerIn, MemoryIn, ReminderSendIn

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
def reminders_generate(background: BackgroundTasks):
    """用 AI 生成今日提醒并入库，随后推送给订阅者。

    推送放在 BackgroundTasks：提醒本身已落库，推送失败不该让接口报错，
    也不该在写事务里做网络请求。
    """
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

    if saved:
        lines = "\n".join(f"· {s['customer']}：{s['content']}" for s in saved)
        background.add_task(
            notifications.dispatch_event, "customer_reminder",
            f"今日熟客提醒（{len(saved)} 条）", lines)
    return {"reminders": saved}


@router.get("/reminders")
def reminders_list(done: int | None = None):
    return db.list_reminders(done)


@router.post("/reminders/{rid}/done")
def reminder_done(rid: int, done: int = 1):
    db.mark_reminder_done(rid, done)
    return {"ok": True}


@router.post("/reminders/{rid}/send")
def reminder_send(rid: int, data: ReminderSendIn | None = None):
    """把一条熟客提醒**真的送出去**（OPC 执行闭环：AI 不只出建议）。

    - channel 留空 → 自动：优先已启用订阅；否则本地记录（mock）→ 零配置也能送达并留痕；
    - 成功/失败都写回 reminders（sent_at/send_channel/send_ok/send_error）并与投递日志同源；
    - 失败返回 502，但**失败原因已入库**，次日自动补发会重试。
    """
    rem = next((r for r in db.list_reminders() if r["id"] == rid), None)
    if not rem:
        raise HTTPException(404, "提醒不存在")
    channel = (data.channel if data else "") or ""
    target = (data.target if data else "") or ""
    content = ((data.content if data else "") or "").strip()
    if not channel:
        channel, target = notifications.auto_channel()
    text = content or rem["content"]
    who = rem.get("customer_name") or "熟客"
    result = notifications.notify(channel, target, f"熟客提醒：{who}", text,
                                  event="customer_reminder")
    db.mark_reminder_sent(rid, channel, bool(result.get("ok")), result.get("error", ""))
    if not result.get("ok"):
        raise HTTPException(502, f"投递失败：{result.get('error')}")
    return {"ok": True, "reminder_id": rid, "channel": channel,
            "attempts": result.get("attempts"), "sent_at": "刚刚"}