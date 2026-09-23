"""基础接口：健康检查 / AI 提供商与设置 / 统一洞察入口"""
from fastapi import APIRouter

import ai
import config
from fastapi import HTTPException

from insight_service import generate as generate_insight
from schemas import SettingsIn, UnifiedInsightIn

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
        "language": s.get("language", "普通话"),
        "ai_pipeline": s.get("ai_pipeline", "fast"),
        "api_profile": s.get("api_profile", "core"),
        "provider": config.detect_provider(s["base_url"]),
    }


@router.post("/settings")
def update_settings(data: SettingsIn):
    """保存 AI 配置到 config.local.json，保存后立即生效（无需重启后端）"""
    s = config.save_settings(
        api_key=data.api_key,
        base_url=data.base_url or None,
        model=data.model or None,
        language=data.language,
        ai_pipeline=data.ai_pipeline,
        api_profile=data.api_profile,
    )
    return {
        "ok": True,
        "ai_enabled": bool(s["api_key"]),
        "base_url": s["base_url"],
        "model": s["model"],
        "language": s.get("language", "普通话"),
        "ai_pipeline": s.get("ai_pipeline", "fast"),
        "api_profile": s.get("api_profile", "core"),
    }


@router.post("/insights")
def unified_insights(data: UnifiedInsightIn):
    """统一 AI 洞察入口：默认同日缓存，失败时返回本地兜底而不是报错。"""
    try:
        return generate_insight(data.scene, data.payload, data.refresh)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
