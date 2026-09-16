"""routers/shops.py — 多店 / 多用户管理接口

覆盖点：
  - 店铺：列表 / 新建（可选择复制现有店的科目结构）/ 改名 / 删除
  - 用户：列表 / 新建（返回一次性令牌）/ 改角色 / 停用
  - 成员关系：授权 / 收回
  - 上下文：当前请求落在哪家店 + 切换默认店

鉴权约定（见 auth.py）：
  - 未设置 SHOP_ACCESS_TOKEN 且没有用户令牌 → 视为本地单店，全部放行（演示/开发）。
  - 配置了全局令牌（店主）→ 全局放行，并可按 X-Shop-Id 指定要操作的店。
  - 携带**用户令牌** → 只能操作自己所属的店；staff 不能管人管店。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

import auth
import shops

router = APIRouter(prefix="/api", tags=["shops"])


# ---------------- 当前身份 / 权限 ----------------

def current_identity(
    request: Request,
    x_shop_token: str | None = Header(default=None, alias="X-Shop-Token"),
    authorization: str | None = Header(default=None),
) -> dict:
    """解析调用者身份。

    返回 {"kind": "owner"} 表示全局令牌（拥有全部权限）；
    返回 {"kind": "local"} 表示本地未启用鉴权（单店/演示，也按全权限处理）；
    返回用户记录 dict 表示某个具体账号。

    为什么"未启用鉴权"要单独一个 kind：只要注册表里存在用户令牌，
    客户端就可能带着它来请求。若不带令牌时直接返回 owner，就会绕过成员校验
    （实测：店员令牌能在管理接口里畅通无阻，还能进任意店铺）。
    所以先查令牌 —— 令牌对得上就是那个用户；只有**没带令牌**时才是本地店主。
    """
    token = (x_shop_token or "").strip()
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    configured = auth.get_configured_token()
    if configured:
        if not token:
            raise HTTPException(status_code=401, detail="访问令牌缺失：请填写访问令牌")
        if auth.token_matches(token, configured):
            return {"kind": "owner", "id": 0, "name": "店主令牌", "role": "owner",
                    "default_shop_id": None}
        user = shops.find_user_by_token(token)
        if not user:
            raise HTTPException(status_code=401, detail="访问令牌缺失或错误")
        return {"kind": "user", **user}
    # 未配置全局令牌（演示/开发）：带用户令牌就按该用户走，否则视为本地店主
    user = shops.find_user_by_token(token) if token else None
    if user:
        return {"kind": "user", **user}
    return {"kind": "local", "id": 0, "name": "本地店主", "role": "owner",
            "default_shop_id": None}


def require_perm(perm: str):
    """依赖工厂：要求当前身份具备某权限（owner/admin/staff 见 shops.ROLE_PERMS）。

    kind=local（未启用鉴权）算本地店主，全权限放行，保持单店/演示可用。
    """

    def _dep(identity: dict = Depends(current_identity)) -> dict:
        role = identity.get("role") or "staff"
        if not shops.has_permission(role, perm):
            raise HTTPException(
                status_code=403,
                detail=f"当前角色「{role}」没有「{perm}」权限，请联系店主")
        return identity

    return _dep


# ---------------- 上下文 ----------------

@router.get("/shops/context")
def shop_context(request: Request,
                  identity: dict = Depends(current_identity)):
    """当前请求正在操作哪家店（前端在标题栏显示店名用）。"""
    shops.init_registry()
    sid = getattr(request.state, "shop_id", None)
    if sid is None:
        sid = shops.current_shop_id()
    if sid is None and identity.get("kind") == "user":
        avail = [s["id"] for s in shops.user_shops(identity["id"])]
        sid = identity.get("default_shop_id") or (avail[0] if avail else None)
    if sid is None:
        # 未启用鉴权（local）时中间件不落在任何店，这里就报默认店
        sid = shops.DEFAULT_SHOP_ID
    shop = shops.get_shop(sid) or {}
    return {
        "shop_id": sid,
        "shop": shop,
        "identity": {"kind": identity.get("kind"), "name": identity.get("name"),
                     "role": identity.get("role")},
        "multi_shop": len([s for s in shops.list_shops() if s["status"] == "active"]) > 1,
        "my_shops": ([s["id"] for s in shops.user_shops(identity["id"])]
                     if identity.get("kind") == "user" else
                     [s["id"] for s in shops.list_shops()]),
    }


@router.post("/shops/switch")
def switch_shop(payload: dict = Body(...),
                identity: dict = Depends(current_identity)):
    """切换当前使用的店（写进用户偏好；staff 也可以切自己所属的店）。"""
    shop_id = payload.get("shop_id")
    if shop_id in (None, ""):
        raise HTTPException(status_code=400, detail="缺少 shop_id")
    try:
        shop_id = int(shop_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="shop_id 必须是整数")
    if not shops.get_shop(shop_id):
        raise HTTPException(status_code=404, detail=f"店铺不存在：{shop_id}")
    if identity.get("kind") == "user":
        if shop_id not in [s["id"] for s in shops.user_shops(identity["id"])]:
            raise HTTPException(status_code=403, detail="你不在该店铺中，无法切换")
        try:
            shops.set_default_shop(identity["id"], shop_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "shop_id": shop_id,
            "shop": shops.get_shop(shop_id),
            "note": ("已记录默认店铺；后续请求请带 X-Shop-Id 头指定店铺"
                     if identity.get("kind") == "user" else
                     "店主令牌可访问全部店铺，请用 X-Shop-Id 头指定要操作的店")}


# ---------------- 店铺 CRUD ----------------

@router.get("/shops")
def api_list_shops(identity: dict = Depends(current_identity)):
    all_shops = shops.list_shops()
    if identity.get("kind") == "user":
        mine = {s["id"]: s["role"] for s in shops.user_shops(identity["id"])}
        all_shops = [s for s in all_shops if s["id"] in mine]
        for s in all_shops:
            s["my_role"] = mine.get(s["id"])
    return {"shops": all_shops, "default_shop_id": shops.DEFAULT_SHOP_ID}


@router.post("/shops")
def api_create_shop(payload: dict = Body(...),
                    identity: dict = Depends(require_perm("manage_shop"))):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写店名")
    copy_from = payload.get("copy_from")
    try:
        shop = shops.create_shop(
            name, note=(payload.get("note") or "").strip(),
            copy_from=int(copy_from) if copy_from not in (None, "") else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 建店人若是有名有姓的账号，顺手把自己加进去，否则新建的店自己都进不去
    if identity.get("kind") == "user":
        shops.grant(shop["id"], identity["id"], identity.get("role") or "owner")
    return {"ok": True, "shop": shop}


@router.put("/shops/{shop_id}")
def api_update_shop(shop_id: int, payload: dict = Body(...),
                    identity: dict = Depends(require_perm("manage_shop"))):
    if not shops.get_shop(shop_id):
        raise HTTPException(status_code=404, detail=f"店铺不存在：{shop_id}")
    try:
        shop = shops.update_shop(shop_id, **payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "shop": shop}


@router.delete("/shops/{shop_id}")
def api_delete_shop(shop_id: int, purge: bool = False,
                    identity: dict = Depends(require_perm("manage_shop"))):
    try:
        return shops.delete_shop(shop_id, purge=purge)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- 用户 CRUD ----------------

@router.get("/shops/users")
def api_list_users(identity: dict = Depends(require_perm("manage_user"))):
    return {"users": shops.list_users()}


@router.post("/shops/users")
def api_create_user(payload: dict = Body(...),
                    identity: dict = Depends(require_perm("manage_user"))):
    try:
        user = shops.create_user(
            payload.get("name") or "", role=payload.get("role") or "staff",
            note=(payload.get("note") or "").strip(),
            shop_ids=[int(x) for x in (payload.get("shop_ids") or [])])
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": user,
            "notice": "令牌只显示这一次，请复制后交给该成员（设置页填写）"}


@router.put("/shops/users/{user_id}")
def api_update_user(user_id: int, payload: dict = Body(...),
                    identity: dict = Depends(require_perm("manage_user"))):
    try:
        return shops.update_user(user_id, **payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/shops/users/{user_id}")
def api_delete_user(user_id: int,
                    identity: dict = Depends(require_perm("manage_user"))):
    return shops.delete_user(user_id)


# ---------------- 成员关系 ----------------

@router.post("/shops/{shop_id}/members")
def api_grant(shop_id: int, payload: dict = Body(...),
              identity: dict = Depends(require_perm("manage_user"))):
    try:
        return shops.grant(shop_id, int(payload.get("user_id")),
                           payload.get("role") or "staff")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/shops/{shop_id}/members/{user_id}")
def api_revoke(shop_id: int, user_id: int,
               identity: dict = Depends(require_perm("manage_user"))):
    return shops.revoke(shop_id, user_id)


@router.get("/shops/{shop_id}/members")
def api_shop_members(shop_id: int,
                     identity: dict = Depends(require_perm("manage_user"))):
    return {"shop_id": shop_id, "members": shops.shop_users(shop_id)}
