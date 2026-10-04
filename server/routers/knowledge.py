"""知识资产治理：总览 + 运行期核验 + 知识包导出（对应参考图「管得住」）。

为什么单独成域：知识资产是**跨业务域**的东西（账本/熟客/库存/票税都会往里写），
挂在任何一个业务域下都会造成"谁都能改"的模糊边界，所以独立成一个可停用的声明式域。

> 文件大小与「知识资产台账」拆开（`routers/knowledge_assets.py`）：
> 同一 `knowledge` 域由本模块统一挂载两个 router，`routers/registry.py` 只登记一次，
> 这样路由文件各自都不超过 arch_check 的 `ROUTER_MAX=80`。
"""
from fastapi import APIRouter, HTTPException, Query

import knowledge_governance as kg
from routers import knowledge_assets as asset_routes
from schemas import KnowledgeVerifyIn

# 同一域的两个 router（registry 按本模块声明的 router 列表挂载，见 routers/registry.py）：
# 台账类接口在 knowledge_assets.py，总览/核验/导出在本文件。
# 不用 include_router 嵌套，是因为本文件与子 router 都自带 `/api` 前缀，
# 嵌套会拼成 `/api/api/...`（踩过一次），改为显式合并路由表。
router = APIRouter(prefix="/api", tags=["knowledge"])
router.routes.extend(asset_routes.router.routes)


@router.get("/knowledge/summary")
def knowledge_summary(verify: bool = Query(default=False)):
    """知识台账总览：资产统计 + 关系索引体检 + （可选）运行期核验。

    verify=true 会触发一次运行期核验并把 volatile 资产的结论回写库，属于**写操作**，
    所以默认关闭；前端"核验"按钮显式传 true。
    """
    return kg.governance_summary(verify=verify)


@router.post("/knowledge/verify")
def knowledge_verify(data: KnowledgeVerifyIn):
    """把 volatile 资产拿回今天的真值上对一遍（漂移的标记为待复核）。

    单条核验时目标不存在返回 404（与 `GET /api/knowledge/assets/{id}` 保持一致）。
    """
    res = kg.verify_runtime(data.asset_id, data.limit)
    if res.get("not_found"):
        raise HTTPException(status_code=404, detail=res.get("error") or "知识资产不存在")
    return res


@router.post("/knowledge/bundle")
def knowledge_bundle():
    """导出知识包（OKF+Git 那一格）：资产 + 关系索引打包成可审计 JSON。

    落盘走 safe_io 原子写、目录固定在 `server/data/knowledge/`（已 gitignore），
    返回 `content` 便于前端直接存文件，也便于评审当场核对包内内容。
    """
    res = kg.export_bundle()
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "导出失败"))
    try:
        with open(res["file"], encoding="utf-8") as f:
            res["content"] = f.read()
    except OSError as e:  # noqa: BLE001 —— 读回失败不影响"已导出"这一事实
        res["content"] = ""
        res["read_error"] = str(e)[:200]
    return res
