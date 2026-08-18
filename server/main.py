"""巷子里的AI掌柜 · 后端服务
启动：uvicorn main:app --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
import ai
import config
from categories import is_known_category

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


class SettingsIn(BaseModel):
    api_key: str = ""          # 传空串 = 清除 Key
    base_url: str = ""
    model: str = ""


# ---------------- 基础接口 ----------------
@app.get("/api/health")
def health():
    return {"status": "ok", "ai": ai.ai_available()}


# ---------------- 设置（用户自行填写 API Key） ----------------
@app.get("/api/settings")
def get_settings():
    """查询当前 AI 配置状态（不返回 Key 本身）"""
    s = config.load_settings()
    return {
        "ai_enabled": bool(s["api_key"]),
        "has_key": bool(s["api_key"]),
        "base_url": s["base_url"],
        "model": s["model"],
    }


@app.post("/api/settings")
def update_settings(data: SettingsIn):
    """保存 AI 配置到 config.local.json，保存后立即生效（无需重启后端）"""
    s = config.save_settings(
        api_key=data.api_key,
        base_url=data.base_url or None,
        model=data.model or None,
    )
    return {
        "ok": True,
        "ai_enabled": bool(s["api_key"]),
        "base_url": s["base_url"],
        "model": s["model"],
    }


# ---------------- 记账 ----------------
@app.post("/api/orders")
def create_order(data: OrderIn):
    """一句话记账：AI解析（含收支分类） + 熟客自动归档 + 生成借贷凭证"""
    parsed = ai.parse_transaction(data.text)
    customer = data.customer or parsed.get("customer", "")
    cid = None
    is_new = False
    if customer:
        cid, is_new = db.find_or_create_customer(
            customer, tags=parsed.get("tags", ""), favorite=parsed.get("item", ""))
    amount = data.amount if data.amount is not None else parsed.get("amount")
    amount_missing = amount is None
    trans_type = parsed.get("trans_type", "income")
    category = parsed.get("category", "")
    # 防御：AI 可能输出未知分类，落到凭证前必须校验，否则用关键词兜底
    if not category or not is_known_category(category):
        category, trans_type = db.detect_category(data.text)
    tid, voucher = db.add_transaction(
        cid, parsed.get("item", "") or data.text, amount,
        trans_type=trans_type, category=category,
        counterparty=customer, note=parsed.get("note", ""))
    return {
        "order_id": tid,
        "parsed": parsed,
        "customer_id": cid,
        "customer_new": cid and is_new,
        "amount_missing": amount_missing,
        "voucher": voucher,
        "friendly_category": db.FRIENDLY_NAMES.get(category, category),
        "summary": db.today_summary(),
    }


@app.get("/api/orders/today")
def orders_today():
    return db.today_summary()


@app.get("/api/orders/monthly")
def orders_monthly(year: int | None = None, month: int | None = None):
    """月度收支汇总 + 分类明细（查账）"""
    return db.monthly_summary(year, month)


@app.get("/api/vouchers")
def vouchers(limit: int = 50):
    """凭证列表（复式记账，借贷分录）"""
    return db.list_vouchers(limit)


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