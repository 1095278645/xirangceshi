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
import logging
import os
import secrets
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("auth")

# 令牌来源（环境变量优先）：未设置或为空 → 鉴权关闭
ENV_TOKEN = "SHOP_ACCESS_TOKEN"

# 受保护前缀：只有 API 需要凭证；页面与静态资源放行以便渲染登录提示
PROTECTED_PREFIXES = ("/api/",)

# 免鉴权路径（精确匹配）：健康检查用于探活/反代后端探测
PUBLIC_PATHS = frozenset({"/api/health"})

# 免鉴权前缀：顾客扫码打开的公开收款页。
# 必须放行 —— 顾客手机上不会有店主设置的访问令牌，若被拦就是"扫码打不开"。
# 安全性由 token 保证：token 是 22 字符随机串（secrets.token_urlsafe(16)），
# 猜不到也枚举不了；该接口只暴露单笔金额与状态，不含任何经营数据。
PUBLIC_PREFIXES = ("/api/pay/", "/pay/")

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
    if p.startswith(PUBLIC_PREFIXES):     # 顾客扫码的公开收款页
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

    多店扩展（见 shops.py）：令牌可以是**用户令牌**而不只是全局令牌。
    校验通过后把「当前店」写进上下文，数据层（db.get_conn）据此切换到该店的库。
    没配置任何令牌时依然整体放行，只做店上下文的解析（不启用多店则等价于原行为）。
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

    def _forbidden(self, request, detail: str) -> JSONResponse:
        headers = {}
        origin = request.headers.get("origin") or ""
        if self._origin_checker and self._origin_checker(origin):
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        return JSONResponse(status_code=403, content={"detail": detail},
                            headers=headers)

    async def dispatch(self, request, call_next):
        if is_public_path(request.url.path):
            # 公开收款页：顾客没有令牌，只能靠收款码 token 反查店铺。
            # 查不到（单店/演示：只在默认店里）就按默认店走，行为与改造前一致。
            shop_id = _public_shop_id(request.url.path)
            if shop_id is None:
                return await call_next(request)
            return await _run_in_shop(shop_id, call_next, request)

        expected = get_configured_token()
        token = extract_token(request.scope)

        if expected and token_matches(token, expected):
            # 全局令牌 = 店主：可以访问任何店（含默认店），但也要按 X-Shop-Id 切库，
            # 否则"店主在多店之间切换"根本不生效（所有请求都打在默认库上）。
            identity = {"kind": "owner", "id": 0, "name": "店主令牌",
                        "role": "owner", "default_shop_id": None}
        else:
            identity = _resolve_identity(token)
            if identity is None:
                if expected:
                    return self._unauthorized(request)
                # 未启用鉴权（演示/单店默认）：也要解析店上下文！
                # 早期这里直接 call_next，导致 X-Shop-Id 被完全忽略 ——
                # 演示环境（不带令牌）切店后数据仍然来自默认店。
                identity = {"kind": "local", "id": 0, "name": "本地店主",
                            "role": "owner", "default_shop_id": None}

        resolution = _resolve_shop_context(identity, request)
        if isinstance(resolution, str):             # 无权限 → 具体原因
            return self._forbidden(request, resolution)
        return await _run_in_shop(resolution, call_next, request)


# ---------------- 多店：令牌 → 用户 → 当前店 ----------------

SHOP_HEADER = "x-shop-id"


def _requested_shop_id(request) -> int | None:
    """客户端指定的店 id：X-Shop-Id 头优先，其次 ?shop_id= 查询参数。"""
    raw = (request.headers.get(SHOP_HEADER) or "").strip()
    if not raw:
        vals = parse_qs((request.scope.get("query_string") or b"").decode("latin-1")
                        ).get("shop_id") or []
        raw = (vals[0] or "").strip() if vals else ""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _resolve_identity(token: str) -> dict | None:
    """按用户令牌解析身份；未启用多店（注册表不存在）时返回 None。

    任何异常都当作「不是用户令牌」处理 —— 鉴权路径绝不能因为注册表问题而 500。
    """
    if not token:
        return None
    try:
        import shops
        user = shops.find_user_by_token(token)
    except Exception as e:  # noqa: BLE001
        log.warning("用户令牌解析失败（按非用户令牌处理）：%s", e)
        return None
    return user


PAY_PREFIX = "/api/pay/"
PAY_PAGE_PREFIX = "/pay/"


def _public_shop_id(path: str) -> int | None:
    """公开收款路径对应的店铺 id（按收款码 token 反查）。

    /pay/<token>            → 公开收款页
    /api/pay/<token>/...    → 该页读取的公开信息
    其余公开路径（/api/health 等）与店铺无关，返回 None。
    """
    p = path or ""
    if p.startswith(PAY_PREFIX):
        token = p[len(PAY_PREFIX):].split("/", 1)[0]
    elif p.startswith(PAY_PAGE_PREFIX):
        token = p[len(PAY_PAGE_PREFIX):].split("/", 1)[0]
    else:
        return None
    try:
        import shops
        return shops.find_shop_by_collection_token(token)
    except Exception as e:  # noqa: BLE001
        log.warning("收款码店铺定位失败（按默认店处理）：%s", e)
        return None


def _resolve_shop_context(identity: dict, request):
    """决定这次请求落在哪家店。

    返回 int（店 id）或 None（无多店上下文）；
    返回 str 表示**拒绝**，字符串是给前端的 403 原因。
    """
    import shops
    if identity.get("role") == "owner":
        # owner 是全局管理员：可以进任何店
        return _requested_shop_id(request) or shops.DEFAULT_SHOP_ID
    allowed = [s["id"] for s in shops.user_shops(identity["id"])]
    if not allowed:
        return "该账号尚未加入任何店铺，请联系店主在「多店管理」里添加"
    want = _requested_shop_id(request)
    if want is None:
        default = identity.get("default_shop_id")
        if default in allowed:
            return default
        return allowed[0]
    if want not in allowed:
        return f"无权访问店铺 {want}（当前账号可用店铺：{allowed}）"
    return want


async def _run_in_shop(shop_id, call_next, request):
    """在指定店的上下文中执行请求，结束后一定复原上下文。"""
    import shops
    token = shops.set_current_shop(shop_id)
    try:
        request.state.shop_id = shop_id
        return await call_next(request)
    finally:
        shops.reset_current_shop(token)
