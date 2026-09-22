"""ratelimit.py — 轻量滑动窗口限流（保护公开接口与语音上传）

## 为什么需要

服务监听 0.0.0.0，且 `/pay/*`、`/api/pay/*`、`/api/voice/transcribe` 是**免鉴权**的
（顾客扫码页不能带店主令牌）。一旦部署到可被公网访问的环境，这些接口可被刷：
语音上传尤其重（要解码 + 跑 ASR）。加一层限流是"企业级"的基本要求。

## 设计取舍

- **纯内存**、零依赖、单进程即够（本项目就是单机部署；多实例需换 Redis）。
- **滑动窗口**（按秒精度），比固定窗口更平滑，避免边界双倍放行。
- 白名单：`/api/health` 始终放行（探活/反代健康检查不应被限流）。
- 粒度：普通 API 与语音分开计数（语音更严），按"客户端 IP"。
- **测试整体放行**：避免影响既有 500+ 用例；限流逻辑由单元测试直接覆盖。
- 可用环境变量调整：`SHOP_RATE_LIMIT=0` 关闭；`SHOP_RATE_LIMIT_PER_MIN`、
  `SHOP_RATE_LIMIT_VOICE_PER_MIN` 调整阈值。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("ratelimit")

DEFAULT_PER_MIN = 240          # 普通 /api/** 接口
DEFAULT_VOICE_PER_MIN = 12     # 语音上传（重）
HEALTH_PATH = "/api/health"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _enabled() -> bool:
    return os.environ.get("SHOP_RATE_LIMIT", "1") not in ("0", "false", "False")


def _is_test_env() -> bool:
    import sys
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def rule_for(path: str) -> str | None:
    """返回该路径命中的限流桶名；None 表示不限流。"""
    if path == HEALTH_PATH:
        return None
    if path == "/api/voice/transcribe":
        return "voice"
    if path.startswith("/api/pay/") or path.startswith("/pay/"):
        return "public_pay"
    if path.startswith("/api/"):
        return "api"
    return None


class SlidingWindowLimiter:
    """按 key（如 IP）统计最近 window 秒内的请求数。线程安全。"""

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = max(1, int(limit))
        self.window = max(1, int(window_seconds))
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._hits.setdefault(key, deque())
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._prune(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """建议的等待秒数（取窗口内最早一次命中的剩余时间）。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._prune(key, now)
            if not q:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_min: int | None = None, voice_per_min: int | None = None):
        super().__init__(app)
        self.per_min = per_min if per_min is not None else _env_int(
            "SHOP_RATE_LIMIT_PER_MIN", DEFAULT_PER_MIN)
        self.voice_per_min = voice_per_min if voice_per_min is not None else _env_int(
            "SHOP_RATE_LIMIT_VOICE_PER_MIN", DEFAULT_VOICE_PER_MIN)
        self._limiters = {
            "api": SlidingWindowLimiter(self.per_min),
            "public_pay": SlidingWindowLimiter(self.per_min),
            "voice": SlidingWindowLimiter(self.voice_per_min),
        }

    def _client_ip(self, request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return (request.client.host if request.client else "unknown")

    async def dispatch(self, request, call_next):
        if not _enabled() or _is_test_env():
            return await call_next(request)
        bucket = rule_for(request.url.path)
        if not bucket:
            return await call_next(request)
        limiter = self._limiters[bucket]
        key = f"{bucket}:{self._client_ip(request)}"
        if limiter.allow(key):
            return await call_next(request)
        wait = limiter.retry_after(key)
        log.warning("限流触发：bucket=%s ip=%s path=%s", bucket, self._client_ip(request),
                    request.url.path)
        return JSONResponse(
            status_code=429,
            content={"detail": f"请求过于频繁，请 {wait} 秒后再试（{bucket} 限流）"},
            headers={"Retry-After": str(wait)},
        )
