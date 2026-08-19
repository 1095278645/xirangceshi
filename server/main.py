"""巷子里的AI掌柜 · 后端服务入口

架构：应用组装（本文件）+ 业务路由（routers/）+ 数据层（db）+ 计算引擎（store/tax）。
启动：
  - uvicorn main:app --host 0.0.0.0 --port 8000
  - python main.py（等效）
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
import db
import heartbeat
import payment
from routers import (arch, basic, customers, orders,
                     payment as payment_router, report, store, tax)

log = logging.getLogger("main")
SYNC_INTERVAL_SECONDS = 6 * 3600   # 每 6 小时自动同步一次昨日账单
HEARTBEAT_INTERVAL_SECONDS = 24 * 3600  # 每天生成一次经营复盘


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
    """后台定时任务：每天生成一次经营复盘，落盘领域上下文供前端/推送取用。"""
    while True:
        try:
            await asyncio.to_thread(heartbeat.generate_daily_review)
        except Exception as e:  # noqa: BLE001
            log.error("heartbeat loop error: %s", e)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    sync_task = asyncio.create_task(_daily_sync_loop())
    hb_task = asyncio.create_task(_heartbeat_loop())
    yield
    sync_task.cancel()
    hb_task.cancel()


app = FastAPI(title="巷子里的AI掌柜", version="0.2.0", lifespan=lifespan)

_STATIC_DIR = config.BASE_DIR / "static"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务路由：按域拆分，路径与原单文件版本完全一致
for r in (arch.router, basic.router, orders.router, customers.router, tax.router,
          store.router, report.router, payment_router.router):
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