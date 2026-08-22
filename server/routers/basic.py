"""基础接口：健康检查 / AI 提供商与设置 / 文案生成"""
from fastapi import APIRouter

import ai
import config
import db
from schemas import CopyIn, SettingsIn

router = APIRouter(prefix="/api", tags=["basic"])


@router.get("/health")
def health():
    return {"status": "ok", "ai": ai.ai_available()}


@router.get("/providers")
def list_providers():
    """返回支持的 AI 大模型提供商列表"""
    return {"providers": config.PROVIDERS}


@router.get("/settings")
def get_settings():
    """查询当前 AI 配置状态（不返回 Key 本身）"""
    s = config.load_settings()
    return {
        "ai_enabled": bool(s["api_key"]),
        "has_key": bool(s["api_key"]),
        "base_url": s["base_url"],
        "model": s["model"],
        "provider": config.detect_provider(s["base_url"]),
    }


@router.post("/settings")
def update_settings(data: SettingsIn):
    """保存 AI 配置到 config.local.json，保存后立即生效（无需重启后端）"""
    s = config.save_settings(
        api_key=data.api_key,
        base_url=data.base_url or None,
        model=data.model or None,
    )
    return {
        "ok": True,
        "ai_enabled": bool(s["api_key"]),
        "base_url": s["base_url"],
        "model": s["model"],
    }


@router.post("/copy")
def copywriting(data: CopyIn):
    # 从 domain_context 读取经营记忆，拼成上下文喂给 AI（无 AI 时填入模板）
    context_parts = []
    review = db.get_domain_context("ledger", "daily_review")
    if review and review.get("value"):
        context_parts.append(str(review["value"])[:200])
    store_diag = db.get_domain_context("store", "diagnosis")
    if store_diag and store_diag.get("value"):
        context_parts.append(str(store_diag["value"])[:200])
    context = " | ".join(context_parts) if context_parts else ""
    text, process, variants = ai.generate_copy(data.shop_name, data.scene, data.extra,
                                                data.customer_name, context, return_process=True)
    return {"text": text, "team": process, "variants": variants,
            "gene_id": (process or {}).get("gene_id")}