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


@router.get("/health/ai")
def health_ai():
    """AI 真实探活：**真的调一次模型**，回答"这套配置到底通不通"。

    为什么需要它：`/api/health` 里的 `ai` 只表示"配了 Key"，不代表 Key 能用。
    线上实测踩过一个很难发现的坑 —— `.env` 把网关的 Key 指向了官方端点，
    于是**所有 AI 调用 401**，而各功能会安静地退回规则兜底：页面照常出内容，
    只是内容变朴素了。这种"静默降级"不主动探一把是发现不了的，
    等到评审现场才发现就晚了。

    与 `/api/health` 的分工：健康检查要保持轻量（Docker healthcheck 每 30 秒打一次），
    所以**不在它里面调模型**；这个端点供「演示前自检 / 部署后验收」显式调用。
    """
    import time
    started = time.time()
    settings = ai.load_settings()
    out = {
        "configured": bool(settings.get("api_key")),
        # base_url 不是密钥，可以直接回显，排障时一眼就能看出"Key 与端点配错了对"
        "base_url": settings.get("base_url", ""),
        "model": settings.get("model", ""),
        "reachable": False,
        "latency_ms": None,
        "error": "",
    }
    if not out["configured"]:
        out["error"] = "未配置 API Key（无 Key 时各功能走规则兜底，属正常降级）"
        return out
    try:
        reply = ai.chat([{"role": "user", "content": "回复两个字：在线"}],
                        max_tokens=16, temperature=0, domain="health_probe")
        out["reachable"] = True
        out["reply"] = (reply or "").strip()[:20]
    except Exception as e:  # noqa: BLE001  探活失败要把原因原样带回去
        out["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    out["latency_ms"] = int((time.time() - started) * 1000)
    return out


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
