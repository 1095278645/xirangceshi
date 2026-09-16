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
    落盘领域上下文供前端/推送取用。"""
    while True:
        try:
            await asyncio.to_thread(heartbeat.generate_daily_review)
        except Exception as e:  # noqa: BLE001
            log.error("heartbeat loop error: %s", e)
        try:
            evo = await asyncio.to_thread(heartbeat.evolution_daily_check)
            if evo and any(evo.get(k) for k in ("promoted", "suppressed", "distilled")):
                log.info("evolution daily check: %s", evo)
        except Exception as e:  # noqa: BLE001
            log.error("evolution daily check error: %s", e)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


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


@app.get("/")
def web_index():
    """手机浏览器打开 http://电脑IP:8000/ 即用"""
    return FileResponse(str(_STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)