"""巷子里的AI掌柜 · 后端服务
启动：uvicorn main:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import ai

app = FastAPI(title="巷子里的AI掌柜", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    db.init_db()


# ---------------- 请求模型 ----------------
class OrderIn(BaseModel):
    text: str                       # 语音转写或手动输入的记账文本
    customer: str = ""              # 可选：手工指定客户
    amount: float | None = None


class MemoryIn(BaseModel):
    customer_id: int
    content: str


class CustomerIn(BaseModel):
    name: str
    phone: str = ""
    tags: str = ""
    favorite: str = ""


class CopyIn(BaseModel):
    shop_name: str = "我的小店"
    scene: str = "今日营业"
    extra: str = ""
    customer_name: str = ""


# ---------------- 基础接口 ----------------
@app.get("/api/health")
def health():
    return {"status": "ok", "ai": ai.ai_available()}


# ---------------- 记账 ----------------
@app.post("/api/orders")
def create_order(data: OrderIn):
    """一句话记账：AI解析 + 熟客自动归档"""
    parsed = ai.parse_transaction(data.text)
    customer = data.customer or parsed.get("customer", "")
    cid = None
    if customer:
        cid, is_new = db.find_or_create_customer(
            customer, tags=parsed.get("tags", ""), favorite=parsed.get("item", ""))
    amount = data.amount if data.amount is not None else parsed.get("amount")
    tid = db.add_transaction(cid, parsed.get("item", "") or data.text, amount or 0,
                             parsed.get("note", ""))
    return {
        "order_id": tid,
        "parsed": parsed,
        "customer_id": cid,
        "customer_new": cid and is_new,
        "summary": db.today_summary(),
    }


@app.get("/api/orders/today")
def orders_today():
    return db.today_summary()


# ---------------- 熟客 ----------------
@app.get("/api/customers")
def customers():
    return db.list_customers()


@app.get("/api/customers/{cid}")
def customer_detail(cid: int):
    c = db.get_customer(cid)
    if not c:
        raise HTTPException(404, "客户不存在")
    return c


@app.post("/api/customers")
def create_customer(data: CustomerIn):
    cid, is_new = db.find_or_create_customer(data.name, data.phone, data.tags, data.favorite)
    return {"customer_id": cid, "is_new": is_new}


@app.post("/api/memories")
def add_memory(data: MemoryIn):
    db.add_memory(data.customer_id, data.content)
    return {"ok": True}


# ---------------- 文案生成 ----------------
@app.post("/api/copy")
def copywriting(data: CopyIn):
    text = ai.generate_copy(data.shop_name, data.scene, data.extra, data.customer_name)
    return {"text": text}


# ---------------- 熟客提醒 ----------------
@app.post("/api/reminders/generate")
def reminders_generate():
    """用 AI 生成今日提醒并入库"""
    customers = db.list_customers()
    brief = "\n".join(
        f"{c['name']}（常点：{c['favorite'] or '未知'}）"
        + ("，最近记忆：" + "；".join(m["content"] for m in db.get_customer(c["id"])["memories"][:3]) if db.get_customer(c["id"])["memories"] else "")
        for c in customers[:20])
    if not brief:
        return {"reminders": []}
    items = ai.generate_reminders(brief)
    saved = []
    for it in items:
        cid, _ = db.find_or_create_customer(it.get("customer", ""))
        rid = db.add_reminder(cid, it.get("content", ""))
        saved.append({"id": rid, "customer": it.get("customer"), "content": it.get("content")})
    return {"reminders": saved}


@app.get("/api/reminders")
def reminders_list(done: int | None = None):
    return db.list_reminders(done)


@app.post("/api/reminders/{rid}/done")
def reminder_done(rid: int, done: int = 1):
    db.mark_reminder_done(rid, done)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)