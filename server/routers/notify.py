"""主动触达：消息订阅 / 推送测试 / 投递记录

对应缺口：原先所有能力都是被动的 —— 店主必须自己想起来打开小程序才能看到
每日复盘与熟客提醒。本路由让这些内容能主动推到店主的手机上。

通道选择建议（见 GET /api/notify/providers）：
  - wecom_bot：企业微信群机器人，**不需要正式 AppID**，配个 webhook 就能收到真消息
  - mock：本地记录，无需任何配置，用于演示与自测
  - wecom_app / wechat_subscribe：需要企业微信应用或正式小程序 AppID
"""
import logging

from fastapi import APIRouter, HTTPException, Query

import notifications
from schemas import NotifySubscriptionIn, NotifyTestIn, WecomBotIn

log = logging.getLogger("routers.notify")
router = APIRouter(prefix="/api", tags=["notify"])


@router.get("/notify/events")
def notify_events():
    """可订阅的事件类型（前端拿来渲染勾选框）。"""
    return {"events": notifications.list_events()}


@router.get("/notify/providers")
def notify_providers():
    """可用通道及其配置要求 —— 让用户知道哪条能立刻用、哪条要资质。"""
    return {"providers": [
        {"id": "mock", "name": notifications.PROVIDER_NAMES["mock"],
         "ready": True, "target_label": "", "hint": "无需配置，推送内容记录在本地文件"},
        {"id": "webhook", "name": notifications.PROVIDER_NAMES["webhook"],
         "ready": True, "target_label": "https://your-host/path 或 ...|共享密钥",
         "hint": "开放 API：把事件以 JSON POST 出去；填 |密钥 则附 HMAC-SHA256 签名，供接收方验签"},
        {"id": "wecom_bot", "name": notifications.PROVIDER_NAMES["wecom_bot"],
         "ready": True, "target_label": "群机器人 Webhook key",
         "hint": "企业微信群 → 添加群机器人 → 复制 Webhook 地址里的 key；不需要正式 AppID"},
        {"id": "wecom_app", "name": notifications.PROVIDER_NAMES["wecom_app"],
         "ready": False, "target_label": "corpid:secret:agentid:用户",
         "hint": "需企业微信管理员创建应用"},
        {"id": "wechat_subscribe", "name": notifications.PROVIDER_NAMES["wechat_subscribe"],
         "ready": False, "target_label": "appid:secret:openid:template_id",
         "hint": "需正式 AppID、用户逐次授权订阅、并在公众平台申请模板；游客模式无法使用"},
    ]}


@router.get("/notify/subscriptions")
def notify_subscriptions():
    return {"subscriptions": notifications.list_subscriptions()}


@router.post("/notify/subscriptions")
def notify_subscription_save(data: NotifySubscriptionIn):
    """新增/更新订阅（通道 + 目标 + 订阅哪些事件）。"""
    try:
        sub = notifications.save_subscription(
            data.channel, data.target, data.events or [],
            enabled=data.enabled, name=data.name, sid=data.sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "subscription": sub}


@router.delete("/notify/subscriptions/{sid}")
def notify_subscription_delete(sid: int):
    if not notifications.delete_subscription(sid):
        raise HTTPException(404, "订阅不存在")
    return {"ok": True}


@router.post("/notify/subscriptions/{sid}/test")
def notify_subscription_test(sid: int):
    """给某个订阅发一条测试消息，立刻验证通道是否通。"""
    subs = [s for s in notifications.list_subscriptions() if s["id"] == sid]
    if not subs:
        raise HTTPException(404, "订阅不存在")
    s = subs[0]
    r = notifications.notify(
        s["channel"], s["target"], "【测试】巷子里的AI掌柜",
        "这是一条测试消息。收到说明推送通道配置正确。", event="manual")
    if not r["ok"]:
        raise HTTPException(502, f"推送失败：{r.get('error')}")
    return {"ok": True, "result": r}


@router.post("/notify/test")
def notify_test(data: NotifyTestIn):
    """不建订阅，直接试发一条（用于快速验证通道）。"""
    r = notifications.notify(data.channel, data.target,
                             data.title or "【测试】巷子里的AI掌柜",
                             data.content or "推送通道测试成功。", event="manual")
    if not r["ok"]:
        raise HTTPException(502, f"推送失败：{r.get('error')}")
    return {"ok": True, "result": r}


@router.post("/notify/wecom-bot")
def notify_set_wecom_bot(data: WecomBotIn):
    """一步配置企业微信群机器人（最省事的真实通道）。

    自动给全部事件建好订阅，并立即发一条测试消息确认通道可用。
    """
    key = (data.key or "").strip()
    if not key:
        raise HTTPException(400, "请填写群机器人 Webhook key")
    try:
        sub = notifications.save_subscription(
            "wecom_bot", key, data.events or [],
            enabled=True, name=data.name or "企业微信群机器人", sid=data.sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    test = notifications.notify(
        "wecom_bot", key, "【已接通】巷子里的AI掌柜",
        "推送通道已配置成功，之后每天的经营复盘与熟客提醒会发到这里。",
        event="manual")
    return {"ok": True, "subscription": sub, "test": test,
            "message": "已接通" if test["ok"] else f"订阅已保存，但测试消息发送失败：{test.get('error')}"}


@router.get("/notify/logs")
def notify_logs(limit: int = Query(default=50, ge=1, le=500), event: str | None = None):
    """投递记录（成功/失败/重试次数/错误都能查，便于排查通道问题）。"""
    try:
        return {"logs": notifications.list_logs(limit=limit, event=event)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/notify/mock-inbox")
def notify_mock_inbox(limit: int = Query(default=20, ge=1, le=200)):
    """本地记录通道收到的消息（演示时用它直观展示"推送到了什么"）。"""
    return {"messages": notifications.recent_mock_messages(limit=limit)}


@router.post("/notify/dispatch")
def notify_dispatch(event: str = Query(...), title: str = Query(default=""),
                    content: str = Query(default=""),
                    business_key: str = Query(default=""),
                    dedup_days: int = Query(default=0, ge=0, le=30)):
    """手动触发一次事件分发（调试与演示用）。

    business_key + dedup_days 可做幂等去重：同一业务键在 N 天内只推一次。
    定时任务用的就是这套（如按日期去重，避免服务重启后重复推送刷屏）。
    """
    try:
        return notifications.dispatch_event(
            event, title or "【手动触发】", content or "",
            business_key=business_key, dedup_days=dedup_days)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
