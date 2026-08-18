"""巷子里的AI掌柜 · 后端服务
启动：uvicorn main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import ai
import config
import payment
import report as reportlib
import tax as taxcalc
from categories import is_known_category, ACCOUNT_TITLES, ACCOUNT_CATEGORY_NAMES

log = logging.getLogger("main")
SYNC_INTERVAL_SECONDS = 6 * 3600   # 每 6 小时自动同步一次昨日账单


async def _daily_sync_loop():
    """后台定时任务：周期性拉取所有启用收款账户的昨日账单。"""
    while True:
        try:
            results = await asyncio.to_thread(payment.run_daily_sync)
            if results:
                log.info("auto sync done: %s", results)
        except Exception as e:  # noqa: BLE001
            log.error("auto sync loop error: %s", e)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    sync_task = asyncio.create_task(_daily_sync_loop())
    yield
    sync_task.cancel()


app = FastAPI(title="巷子里的AI掌柜", version="0.1.0", lifespan=lifespan)

_STATIC_DIR = config.BASE_DIR / "static"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class VatIn(BaseModel):
    quarterly_revenue: float   # 季度销售额


class SurtaxIn(BaseModel):
    vat: float                 # 实缴增值税
    is_small: bool = True      # 是否小规模纳税人


class PitIn(BaseModel):
    salary: float              # 月工资
    social_insurance: float = 0
    special_deduction: float = 0


class CitIn(BaseModel):
    annual_income: float       # 年应纳税所得额
    is_small: bool = True      # 是否小微企业


class PaymentSourceIn(BaseModel):
    """收款账户（微信商户号 / 聚合支付）"""
    sid: int | None = None     # 有值=更新
    source_type: str = "wechat"   # wechat / aggregate
    name: str = ""
    mchid: str = ""
    appid: str = ""
    cert_path: str = ""
    private_key_path: str = ""
    api_v3_key: str = ""
    enabled: bool = False


# ---------------- 基础接口 ----------------
@app.get("/api/health")
def health():
    return {"status": "ok", "ai": ai.ai_available()}


# ---------------- 设置（用户自行填写 API Key） ----------------
@app.get("/api/providers")
def list_providers():
    """返回支持的 AI 大模型提供商列表"""
    return {"providers": config.PROVIDERS}


@app.get("/api/settings")
def get_settings():
    """查询当前 AI 配置状态（不返回 Key 本身）"""
    s = config.load_settings()
    return {
        "ai_enabled": bool(s["api_key"]),
        "has_key": bool(s["api_key"]),
        "base_url": s["base_url"],
        "model": s["model"],
        "provider": config.detect_provider(s["base_url"]),
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
    # 安全护栏：边界场景优先，其次大额检测
    safety_warning = taxcalc.detect_boundary(data.text) or taxcalc.check_amount_guard(amount)
    return {
        "order_id": tid,
        "parsed": parsed,
        "customer_id": cid,
        "customer_new": cid and is_new,
        "amount_missing": amount_missing,
        "voucher": voucher,
        "friendly_category": db.FRIENDLY_NAMES.get(category, category),
        "summary": db.today_summary(),
        "safety_warning": safety_warning,
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


# ---------------- 科目表（省账通：68科目台账） ----------------
@app.get("/api/account-titles")
def account_titles():
    """小企业会计准则 68 科目表，按类别分组"""
    by_cat: dict[str, list] = {}
    for code, name, cat, _direction, level in ACCOUNT_TITLES:
        by_cat.setdefault(cat, []).append({"code": code, "name": name, "level": level})
    return {
        "total": len(ACCOUNT_TITLES),
        "categories": [
            {"category": cat, "name": ACCOUNT_CATEGORY_NAMES.get(cat, cat), "titles": items}
            for cat, items in by_cat.items()
        ],
    }


# ---------------- 查账：交易流水 ----------------
@app.get("/api/transactions")
def transactions(year: int | None = None, month: int | None = None, limit: int = 100):
    """月度交易流水（大白话分类名），默认当月"""
    return db.list_transactions(year, month, limit)


# ---------------- 税法计算（省账通能力） ----------------
@app.post("/api/tax/vat")
def tax_vat(data: VatIn):
    """增值税（小规模）：季度销售额≤30万免征"""
    return taxcalc.calc_vat(data.quarterly_revenue)


@app.post("/api/tax/surtax")
def tax_surtax(data: SurtaxIn):
    """附加税：城建+教育+地方教育，小规模六税两费减半"""
    return taxcalc.calc_surtax(data.vat, data.is_small)


@app.post("/api/tax/pit")
def tax_pit(data: PitIn):
    """个人所得税：工资薪金 7级超额累进"""
    return taxcalc.calc_individual_income_tax(
        data.salary, data.social_insurance, data.special_deduction)


@app.post("/api/tax/cit")
def tax_cit(data: CitIn):
    """企业所得税：小微企业分段（5%/10%），否则 25%"""
    return taxcalc.calc_corporate_income_tax(data.annual_income, data.is_small)


@app.get("/api/tax/calendar")
def tax_calendar(year: int | None = None, month: int | None = None):
    """当月报税日历提醒"""
    return taxcalc.get_filing_calendar(year, month)


# ---------------- 报表导出（省账通能力） ----------------
@app.get("/api/report/monthly")
def report_monthly(year: int | None = None, month: int | None = None):
    """导出月度收支 Excel 报表"""
    result = reportlib.get_monthly_report(year, month)
    if "error" in result:
        raise HTTPException(500, result["error"])
    return FileResponse(result["file"], filename=Path(result["file"]).name)


# ---------------- 收款账户（二维码流水同步） ----------------
@app.get("/api/payment/sources")
def payment_sources():
    """收款账户列表（微信商户号 / 聚合支付）"""
    return {"sources": db.list_payment_sources()}


@app.post("/api/payment/sources")
def payment_source_save(data: PaymentSourceIn):
    """新增/更新收款账户。mchid 填 DEMO 即为演示模式（免商户资料体验全流程）"""
    if data.source_type not in ("wechat", "aggregate"):
        raise HTTPException(400, "source_type 仅支持 wechat / aggregate")
    sid = db.save_payment_source(
        source_type=data.source_type, name=data.name, mchid=data.mchid,
        appid=data.appid, cert_path=data.cert_path,
        private_key_path=data.private_key_path, api_v3_key=data.api_v3_key,
        enabled=data.enabled, sid=data.sid)
    return {"ok": True, "id": sid}


@app.delete("/api/payment/sources/{sid}")
def payment_source_delete(sid: int):
    db.delete_payment_source(sid)
    return {"ok": True}


@app.post("/api/payment/sources/{sid}/sync")
def payment_source_sync(sid: int, bill_date: str | None = None):
    """手动同步某账户账单（默认昨天）。"""
    try:
        result = payment.run_sync(sid, bill_date)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return result


@app.get("/api/payment/logs")
def payment_logs(limit: int = 30):
    """账单同步日志"""
    return {"logs": db.list_sync_logs(limit)}


@app.post("/api/payment/demo-clear")
def payment_demo_clear():
    """一键清空演示模式产生的流水（wx_trade_id 以 DEMO- 开头）"""
    n = payment.demo_clear()
    return {"ok": True, "deleted": n}


@app.post("/api/payment/sync-all")
def payment_sync_all():
    """手动触发一次全部启用账户的昨日账单同步"""
    return {"results": payment.run_daily_sync()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


# ---------------- 网页端（手机浏览器访问） ----------------
# 放在最后：API 路由优先匹配，静态资源兜底
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def web_index():
    """手机浏览器打开 http://电脑IP:8000/ 即用"""
    return FileResponse(str(_STATIC_DIR / "index.html"))