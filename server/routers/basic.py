"""基础接口：健康检查 / AI 提供商与设置 / 文案生成"""
from fastapi import APIRouter

import ai
import config
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
    text = ai.generate_copy(data.shop_name, data.scene, data.extra, data.customer_name)
    return {"text": text}