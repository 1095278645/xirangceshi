"""auth.py — 访问令牌鉴权（可选启用，默认关闭）

为什么需要：服务默认监听 0.0.0.0:8000，且所有业务接口原本无任何鉴权。
同一局域网（或做了端口转发后的公网）任何设备都能读取全部经营流水、熟客
记忆与账单，并能改写 AI 配置、写入收款账户的证书路径。

设计取舍：
  - **默认关闭**：未设置 SHOP_ACCESS_TOKEN 时完全放行，行为与改造前一致，
    不破坏既有部署；设置了才强制校验（显式 opt-in，避免升级即断服务）。
  - **只守 /api/**：首页与 /static 资源放行，保证浏览器能加载页面并渲染出
    「输入访问令牌」的界面；真正的数据与写操作全部在 /api 之下。
  - **请求头传递**：X-Shop-Token 或 Authorization: Bearer <token>。
    另支持 ?token= 查询参数，仅用于浏览器直接下载报表这类无法自定义头部的场景。
  - **恒定时间比较**：用 hmac.compare_digest 防时序侧信道。
  - **与报表落盘的关系**：报表按 Filename 返回，鉴权失败时不会泄露文件。
"""
from __future__ import annotations

import hmac
import os
import secrets
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# 令牌来源（环境变量优先）：未设置或为空 → 鉴权关闭
ENV_TOKEN = "SHOP_ACCESS_TOKEN"

# 受保护前缀：只有 API 需要凭证；页面与静态资源放行以便渲染登录提示
PROTECTED_PREFIXES = ("/api/",)

# 免鉴权路径（精确匹配）：健康检查用于探活/反代后端探测
PUBLIC_PATHS = frozenset({"/api/health"})

# 接受令牌的请求头（任一命中即可）
TOKEN_HEADERS = ("x-shop-token", "x-access-token")

# 查询参数名：仅供无法设置头部的直接下载场景
TOKEN_QUERY_KEY = "token"


def generate_token(nbytes: int = 32) -> str:
    """生成一个高强度 URL 安全令牌（46 字符左右），供部署者写入环境变量。"""
    return secrets.token_urlsafe(nbytes)


def get_configured_token() -> str:
    """读取当前配置的令牌；返回空串表示「鉴权未启用」。"""
    return (os.environ.get(ENV_TOKEN) or "").strip()


def auth_enabled() -> bool:
    """鉴权是否启用（未配置令牌即视为关闭，保持向后兼容）。"""
    return bool(get_configured_token())


def is_public_path(path: str) -> bool:
    """该路径是否免鉴权。只有 /api/ 前缀受保护，其余（页面/静态资源）放行。"""
    p = path or "/"
    if p in PUBLIC_PATHS:
        return True
    return not any(p.startswith(pref) for pref in PROTECTED_PREFIXES)


def extract_token(scope) -> str:
    """从请求中取出客户端提交的令牌（头优先，其次查询参数）。"""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1")
               for k, v in scope.get("headers") or []}
    for name in TOKEN_HEADERS:
        val = (headers.get(name) or "").strip()
        if val:
            return val
    auth = (headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # 查询参数兜底（浏览器直接下载报表等无法自定义头部的场景）
    qs = (scope.get("query_string") or b"").decode("latin-1")
    if qs:
        vals = parse_qs(qs).get(TOKEN_QUERY_KEY) or []
        if vals:
            return (vals[0] or "").strip()
    return ""


def token_matches(provided: str, expected: str | None = None) -> bool:
    """恒定时间比较；expected 为空表示未配置（视为放行）。"""
    expected = get_configured_token() if expected is None else expected
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


class AccessTokenMiddleware(BaseHTTPMiddleware):
    """在 /api/ 上强制校验访问令牌；未配置令牌时整体放行。

    origin_checker: 可选的 (origin: str) -> bool，用于在 401 响应上补 CORS 头。
    之所以需要它：本中间件比 CORSMiddleware 更靠内层，401 是直接返回的，
    不会经过 CORS 中间件；若不补头，白名单内的手机浏览器会因缺少
    Access-Control-Allow-Origin 而读不到 401 详情，只能显示笼统的失败提示。
    """

    def __init__(self, app, origin_checker=None):
        super().__init__(app)
        self._origin_checker = origin_checker

    def _unauthorized(self, request) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"}
        origin = request.headers.get("origin") or ""
        if self._origin_checker and self._origin_checker(origin):
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        return JSONResponse(
            status_code=401,
            content={"detail": "访问令牌缺失或错误：请在「设置」页填写访问令牌"},
            headers=headers,
        )

    async def dispatch(self, request, call_next):
        expected = get_configured_token()
        if not expected or is_public_path(request.url.path):
            return await call_next(request)
        if not token_matches(extract_token(request.scope), expected):
            return self._unauthorized(request)
        return await call_next(request)
