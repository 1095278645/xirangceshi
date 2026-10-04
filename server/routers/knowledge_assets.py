"""知识资产台账读写：列表 / 单条查证 / 手工登记 / 回填。

「查到源头」的落点：`/{asset_id}/sources` 返回这件知识当初依据的原始事实与来源定位，
评审问"你凭什么这么说"时，从这里一路点到原始数据。
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

import knowledge_assets as ka
from schemas import KnowledgeAssetIn

router = APIRouter(prefix="/api", tags=["knowledge"])


@router.get("/knowledge/assets")
def list_knowledge_assets(
    # 铁律5：有限取值一律 Literal —— 非法值在请求期 422，而不是静默返回空集
    # （原先用自由文本，`?kind=想当然` 会 200 + count=0，看起来"没有这类知识"，
    #   其实是用错了枚举值；这类静默失败最难排查）。
    kind: Literal["", "decision", "experience", "strategy", "attention", "fact", "profile"] = "",
    state: Literal["", "all", "draft", "active", "superseded", "retired"] = "active",
    volatility: Literal["", "stable", "slow", "volatile"] = "",
    subject: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
):
    """知识资产列表（state 传 all 表示不过滤；非法枚举在请求期即被拒）。"""
    items = ka.list_assets(kind=kind or None, state=None if state == "all" else (state or None),
                           volatility=volatility or None, subject=subject or None, limit=limit)
    return {"items": ka.expose_active(items), "count": len(items)}


@router.get("/knowledge/assets/{asset_id}")
def get_knowledge_asset(asset_id: str):
    """单条知识资产（含证据与版本信息）。"""
    asset = ka.get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="知识资产不存在")
    return asset


@router.get("/knowledge/assets/{asset_id}/sources")
def asset_sources(asset_id: str):
    """这条知识从哪来、依据什么事实、现在还行不行？—— 可查证入口。"""
    asset = ka.get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="知识资产不存在")
    return {
        "asset_id": asset_id, "kind": asset.get("kind"), "subject": asset.get("subject"),
        "statement": asset.get("statement"), "source_kind": asset.get("source_kind"),
        "source_ref": asset.get("source_ref"), "evidence": asset.get("evidence") or [],
        "version": asset.get("version"), "state": asset.get("state"),
        "volatility": asset.get("volatility"), "verified_at": asset.get("verified_at"),
        "verify_ok": asset.get("verify_ok"), "drift_note": asset.get("drift_note") or "",
    }


@router.post("/knowledge/assets")
def register_asset(data: KnowledgeAssetIn):
    """手工登记一件知识资产（AI 自动登记的路径见 knowledge_extract）。"""
    try:
        res = ka.register_asset(
            data.kind, data.subject, data.statement, evidence=data.evidence,
            source_kind=data.source_kind, source_ref=data.source_ref,
            volatility=data.volatility, confidence=data.confidence)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"created": res.get("created"), "changed": res.get("changed"),
            "superseded": res.get("superseded"), "asset": res.get("asset")}


@router.post("/knowledge/backfill")
def backfill_knowledge():
    """把进化层已有的基因/经验回填登记为知识资产（让存量知识也进入治理视野）。"""
    return ka.backfill()
