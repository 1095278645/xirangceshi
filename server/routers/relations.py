"""跨域关系索引：这张店的关系地图 + 线索追溯 + 增量合并（参考图「看得懂」）。

为什么不塞进业务路由：关系天然是**跨域**的（熟客→流水→商品→供应商→发票），
它不属于任何一个业务域，独立成域才能在不影响既有业务的前提下增删。

**降级原则**：关系索引是增强层，知识资产是主链路。任何一处读不到
（表未建 / 迁移失败 / 库抖动）都返回 `available=false` + 原因，而不是把 500 抛给前端 ——
独立复核实测过：删掉 `knowledge_edges` 后本域三个 GET 全 500，而知识域正常降级。
"""
from typing import Literal

from fastapi import APIRouter, Query

import shop_relations as sr

router = APIRouter(prefix="/api", tags=["relations"])

# 实体类型有限取值（与 shop_relations.ENTITY_TYPES 一致，由 test_knowledge_http 回归）
EntityType = Literal["customer", "product", "supplier", "invoice", "transaction", "debt", "unknown"]


def _guard(fn, *args, **kwargs) -> dict:
    """执行关系层调用；失败时降级成可读结果（不 500）。"""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 —— 关系层是增强层，任何异常都不该让接口挂
        return {"available": False, "error": str(e)[:200],
                "note": "关系索引当前不可用（表未建/迁移未完成/库抖动），知识台账不受影响。"}


@router.get("/relations/summary")
def relations_summary():
    """关系索引体检：有效边数、类型分布、健康度（孤儿边/悬空对象/重复键/陈旧边）。"""
    return _guard(sr.relations_summary)


@router.get("/relations/graph")
def relations_graph(keyword: str = Query(default=""), limit: int = Query(default=30, ge=1, le=200)):
    """关系地图：按实体类型分组的节点 + 最近的关系边（中文关系名，前端不再翻译）。"""
    return _guard(sr.entity_landscape, keyword=keyword, limit=limit)


@router.get("/relations/chain")
def relations_chain(entity_type: EntityType = Query(default="customer"),
                    entity_ref: str = Query(default=""), depth: int = Query(default=2, ge=1, le=3)):
    """追一条线索：从某个实体出发，看它和谁有关、怎么串起来的。"""
    return _guard(sr.knowledge_chain, entity_type, entity_ref, depth=depth)


@router.post("/relations/merge")
def relations_merge(full: bool = Query(default=False), confirm: bool = Query(default=False)):
    """增量合并（LightRAG 思想）：重算抽取 → upsert → 未再证实的边软删。

    默认（`full=false`）只覆盖扫描窗口内的边；`full=true` 会忽略流水扫描上限、
    按**全库**重算。回收（软删不在场的旧边）是**破坏性动作**，按铁律2 需要
    `full=true` **且** `confirm=true`；少任何一个都只做增量 upsert，并返回 `skipped`。
    """
    return _guard(sr.merge_relations, full=full, confirm=confirm)
