"""巷子里的AI掌柜 · 后端服务入口

架构：应用组装（本文件）+ 业务路由（routers/）+ 数据层（db）+ 计算引擎（store/tax）。
启动：
  - uvicorn main:app --host 0.0.0.0 --port 8000
  - python main.py（等效）
"""
import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import auth
import backup
import config
import db
import heartbeat
import notifications
import payment
from routers import registry

log = logging.getLogger("main")
SYNC_INTERVAL_SECONDS = 6 * 3600   # 每 6 小时自动同步一次昨日账单
HEARTBEAT_INTERVAL_SECONDS = 24 * 3600  # 每天生成一次经营复盘
BACKUP_INTERVAL_SECONDS = 6 * 3600      # 每 6 小时检查一次是否需要自动备份（实际按天节流）


async def _backup_loop():
    """后台定时任务：定期为数据库做一致性快照。

    账本是财务数据，原先只存在单个 SQLite 文件里、没有任何备份手段。
    这里按天节流（auto_backup_if_needed 内部判断间隔），并在启动时立即备份一次，
    避免"服务刚起来就崩"这种窗口期没有可用备份。
    """
    first = True
    while True:
        try:
            info = await asyncio.to_thread(backup.auto_backup_if_needed)
            if info:
                log.info("自动备份完成：%s（%.1f KB）", info["name"], info["size"] / 1024)
        except Exception as e:  # noqa: BLE001
            log.error("自动备份失败：%s", e)
        # 首次启动后等一小会儿再循环，避免与建库/迁移抢 IO
        await asyncio.sleep(30 if first else BACKUP_INTERVAL_SECONDS)
        first = False


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


async def _heartbeat_loop():
    """后台定时任务：每天生成经营复盘 + 跑进化检查（经验晋升/基因抑制/技能蒸馏），
    落盘领域上下文供前端/推送取用。

    生成后主动推送给订阅者 —— 这是"主动触达"的定时触发点：原先复盘只躺在
    数据库里，店主不看就等于不存在。
    """
    first = True
    while True:
        try:
            text = await asyncio.to_thread(heartbeat.generate_daily_review)
            # 幂等：同一业务日期内只推一次，避免重启/重复触发刷屏
            await asyncio.to_thread(
                notifications.dispatch_event, "daily_review",
                "今日经营复盘", text or "",
                business_key=_today_key(), dedup_days=1)
            await asyncio.to_thread(_maybe_warn_revenue)
        except Exception as e:  # noqa: BLE001
            log.error("heartbeat loop error: %s", e)
        try:
            evo = await asyncio.to_thread(heartbeat.evolution_daily_check)
            if evo and any(evo.get(k) for k in ("promoted", "suppressed", "distilled")):
                log.info("evolution daily check: %s", evo)
        except Exception as e:  # noqa: BLE001
            log.error("evolution daily check error: %s", e)
        await asyncio.sleep(60 if first else HEARTBEAT_INTERVAL_SECONDS)
        first = False


def _today_key() -> str:
    from datetime import date
    return date.today().isoformat()


def _maybe_warn_revenue() -> None:
    """流水异常预警：日流水低于保本线时主动提醒。

    这是"主动触达"里最有价值的一条 —— 店主不会天天去看保本线，
    但一旦掉到线下就是"开门一天亏一天"。
    """
    try:
        profile = heartbeat._latest_profile()
        if not profile:
            return
        storelib = __import__("store")
        res = storelib.calc_store_model(
            gross_margin=profile.get("gross_margin"),
            rent=profile.get("rent") or 0,
            salary=profile.get("salary") or 0,
            utilities=profile.get("utilities") or 0,
            total_investment=profile.get("total_investment") or 0,
            cash_on_hand=profile.get("cash_on_hand") or 0,
            biz_type=profile.get("biz_type") or "餐饮")
        break_even = (res.get("model") or {}).get("break_even_day")
        if not break_even:
            return
        today = db.today_summary()
        # 只提醒有明显营业的情况，避免一早还没开门就误报
        if today["cnt"] < 3:
            return
        if today["income"] < break_even:
            gap = break_even - today["income"]
            notifications.dispatch_event(
                "revenue_warning",
                f"今日流水低于保本线（{_today_key()}）",
                f"今天收了 {today['income']:,.0f} 元，保本线 {break_even:,.0f} 元，"
                f"差 {gap:,.0f} 元。今天开门是亏的，看看能不能多做几单。",
                business_key=_today_key(), dedup_days=1)
    except Exception as e:  # noqa: BLE001
        log.warning("流水预警检查失败：%s", e)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    sync_task = asyncio.create_task(_daily_sync_loop())
    hb_task = asyncio.create_task(_heartbeat_loop())
    backup_task = asyncio.create_task(_backup_loop())
    yield
    sync_task.cancel()
    hb_task.cancel()
    backup_task.cancel()


app = FastAPI(title="巷子里的AI掌柜", version="0.2.0", lifespan=lifespan)

_STATIC_DIR = config.BASE_DIR / "static"

# CORS：仅放行本机与内网来源（手机浏览器访问 http://电脑IP:8000 时 Origin 为局域网 IP）。
# 小程序 wx.request 不受浏览器 CORS 限制，无需放行。避免公网恶意网页调用本地 API。
_ORIGIN_RE = r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$"


def _is_allowed_origin(origin: str) -> bool:
    """该 Origin 是否属于放行范围（本机 / 局域网网段）。

    与 CORSMiddleware 的 _ORIGIN_RE 共用同一套规则。单独抽成函数是为了让
    鉴权中间件在返回 401 时也能补上 CORS 头 —— 否则白名单内的手机浏览器读不到
    401 详情，前端只能显示笼统的「请求失败」，无法提示用户去填访问令牌。
    """
    return bool(origin) and re.match(_ORIGIN_RE, origin) is not None


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_ORIGIN_RE,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 访问令牌鉴权：只守 /api/**，页面与静态资源放行。
# 未设置 SHOP_ACCESS_TOKEN 时整体放行（行为与改造前一致，不破坏既有部署）。
# 注意中间件顺序：Starlette 后添加者先执行 → 本行在 CORS 之后添加，
# 因此 CORS 先处理（含 OPTIONS 预检），再由鉴权拦截实际 API 调用。
app.add_middleware(auth.AccessTokenMiddleware, origin_checker=_is_allowed_origin)

# 业务路由：按域拆分，由 registry 声明式注册表统一挂载。
# 新增/停用/删除业务域只改 routers/registry.py 的 BUSINESS_DOMAINS 声明，
# 本文件与各域流程代码均无需改动（对标 team_domains 的声明式注册表思想）。
for r in registry.get_routers():
    app.include_router(r)


# ---------------- 网页端（手机浏览器访问） ----------------
# API 路由已在上方注册，静态资源放最后兜底
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/pay/{token}")
def pay_page_short(token: str):
    """收款短链：顾客扫码直接打开（与 /api/pay/{token} 同一个页面）。

    放在 /pay/ 而不是 /api/ 下，既更短好扫，也天然不在"只守 /api/"的
    鉴权范围内（顾客手机上没有店主令牌）。
    """
    from routers.collect import _PAY_PAGE
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_PAY_PAGE)


@app.get("/")
def web_index():
    """手机浏览器打开 http://电脑IP:8000/ 即用"""
    return FileResponse(str(_STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)