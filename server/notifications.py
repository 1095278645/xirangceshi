"""notifications.py — 主动触达：订阅、分发、投递日志（通道适配见 notify_channels.py）"""
from __future__ import annotations

import logging
import os
import threading

# 通道适配已外移到 notify_channels（L1 单文件瘦身）；re-export 保持对外接口不变
from notify_channels import (  # noqa: F401
    PROVIDERS, _send_via, _send_webhook, _mock_path, MOCK_FILE, recent_mock_messages,
)

log = logging.getLogger("notifications")

__all__ = [
    "EVENTS", "list_events", "notify", "dispatch_event", "dispatch_event_async",
    "list_subscriptions", "save_subscription", "delete_subscription",
    "list_logs", "recent_mock_messages", "PROVIDER_NAMES",
]

EVENTS = {
    "daily_review": {
        "name": "每日经营复盘",
        "desc": "当天收支、本月累计、单店一句话",
        "trigger": "每天定时（heartbeat）",
    },
    "customer_reminder": {
        "name": "熟客提醒",
        "desc": "今天该惦记哪位熟客、说什么",
        "trigger": "每天定时或手动生成提醒后",
    },
    "payment_received": {
        "name": "收款到账",
        "desc": "确认收款后即时播报",
        "trigger": "收款确认入账时",
    },
    "revenue_warning": {
        "name": "流水异常预警",
        "desc": "日流水明显低于保本线时提醒",
        "trigger": "每天定时",
    },
    "order_created": {
        "name": "记账成功",
        "desc": "每成功记一笔账时（可用于对接 ERP / 代账系统 / 自有看板）",
        "trigger": "记账成功后",
    },
}

PROVIDER_NAMES = {
    "mock": "本地记录（演示/自测用）",
    "webhook": "自定义 Webhook（开放 API，POST JSON）",
    "wecom_bot": "企业微信群机器人（推荐，无需 AppID）",
    "wecom_app": "企业微信应用消息（需 corpid/secret/agentid）",
    "wechat_subscribe": "微信小程序订阅消息（需正式 AppID 与模板）",
}

MAX_RETRY = 3


def list_events() -> list[dict]:
    return [{"event": k, **v} for k, v in EVENTS.items()]



def _log_delivery(event: str, channel: str, target: str, title: str,
                  content: str, ok: bool, attempts: int, error: str = "") -> None:
    from db import get_conn
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO notification_logs(event, channel, target, title, content, "
                "ok, attempts, error) VALUES(?,?,?,?,?,?,?,?)",
                (event, channel, target, title, content, 1 if ok else 0, attempts, error))
    except Exception as e:  # noqa: BLE001
        log.warning("写投递日志失败：%s", e)


def notify(channel: str, target: str, title: str, content: str,
           event: str = "manual", retries: int = MAX_RETRY) -> dict:
    """向单个通道投递一条消息（带重试与日志）。"""
    attempts = 0
    last_error = ""
    for i in range(max(1, retries)):
        attempts = i + 1
        try:
            result = _send_via(channel, target, title, content, event)
            _log_delivery(event, channel, target, title, content, True, attempts)
            return {"ok": True, "channel": channel, "attempts": attempts,
                    "detail": result.get("detail")}
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            log.warning("推送失败（%s/%s 第 %d 次）：%s", channel, event, attempts, e)
    _log_delivery(event, channel, target, title, content, False, attempts, last_error)
    return {"ok": False, "channel": channel, "attempts": attempts,
            "error": last_error}


# ---------------- 订阅与分发 ----------------

def list_subscriptions(enabled_only: bool = False) -> list[dict]:
    from db import get_conn
    with get_conn() as conn:
        sql = "SELECT * FROM notification_subscriptions"
        if enabled_only:
            sql += " WHERE enabled=1"
        rows = conn.execute(sql + " ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["events"] = [e for e in (d.get("events") or "").split(",") if e]
        out.append(d)
    return out


def save_subscription(channel: str, target: str, events: list[str],
                      enabled: bool = True, name: str = "", sid: int | None = None) -> dict:
    """新增/更新一条订阅。events 为空表示订阅全部事件。"""
    from db import get_conn
    if channel not in PROVIDERS:
        raise ValueError(f"未知通道：{channel}（可用：{sorted(PROVIDERS)}）")
    bad = [e for e in events if e not in EVENTS]
    if bad:
        raise ValueError(f"未知事件：{bad}（可用：{sorted(EVENTS)}）")
    if channel != "mock" and not (target or "").strip():
        raise ValueError(f"通道 {channel} 需要填写接收目标（如 webhook key）")
    events_str = ",".join(events)
    with get_conn() as conn:
        if sid:
            conn.execute(
                "UPDATE notification_subscriptions SET channel=?, target=?, events=?, "
                "enabled=?, name=? WHERE id=?",
                (channel, target, events_str, 1 if enabled else 0, name, sid))
            new_id = sid
        else:
            cur = conn.execute(
                "INSERT INTO notification_subscriptions(channel, target, events, enabled, name) "
                "VALUES(?,?,?,?,?)",
                (channel, target, events_str, 1 if enabled else 0, name))
            new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM notification_subscriptions WHERE id=?",
                           (new_id,)).fetchone()
    d = dict(row)
    d["events"] = [e for e in (d.get("events") or "").split(",") if e]
    log.info("订阅已保存 id=%s 通道=%s 事件=%s", new_id, channel, d["events"] or "全部")
    return d


def delete_subscription(sid: int) -> bool:
    from db import get_conn
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM notification_subscriptions WHERE id=?", (sid,))
        return cur.rowcount > 0


def dispatch_event(event: str, title: str, content: str,
                   business_key: str = "", dedup_days: int = 0) -> dict:
    """把一条事件推给所有订阅了它的通道。

    dedup_days > 0 时做幂等去重：同一 (event, business_key) 在 N 天内只推一次，
    避免服务重启/重复触发导致刷屏。
    """
    if event not in EVENTS:
        raise ValueError(f"未知事件：{event}（可用：{sorted(EVENTS)}）")
    if dedup_days > 0 and business_key:
        if _already_sent(event, business_key, dedup_days):
            log.info("事件 %s/%s 在 %d 天内已推送过，跳过", event, business_key, dedup_days)
            return {"ok": True, "skipped": True, "reason": "dedup", "results": []}

    subs = [s for s in list_subscriptions(enabled_only=True)
            if not s["events"] or event in s["events"]]
    if not subs:
        log.info("事件 %s 没有订阅者，未推送（内容仍已落库）", event)
        return {"ok": True, "skipped": True, "reason": "no_subscriber", "results": []}

    results = []
    for s in subs:
        r = notify(s["channel"], s["target"], title, content, event=event)
        r["subscription_id"] = s["id"]
        results.append(r)
    ok = all(r["ok"] for r in results) if results else True
    return {"ok": ok, "skipped": False, "event": event, "results": results}


def dispatch_event_async(event: str, title: str, content: str,
                         business_key: str = "", dedup_days: int = 0) -> None:
    """异步分发到后台守护线程（fire-and-forget）。

    用于「记账成功」这类**不该拖慢主流程**的事件：若同步等 webhook 返回，
    弱网下店主每记一笔就要多等几秒。异常只记日志，不影响调用方。
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return   # 测试里不起后台线程，避免线程与临时库清理竞态

    def _run() -> None:
        try:
            dispatch_event(event, title, content,
                           business_key=business_key, dedup_days=dedup_days)
        except Exception as e:  # noqa: BLE001
            log.warning("异步事件分发失败 %s：%s", event, e)

    threading.Thread(target=_run, name=f"dispatch-{event}", daemon=True).start()


def _already_sent(event: str, business_key: str, days: int) -> bool:
    from db import get_conn
    if days <= 0:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notification_logs WHERE event=? AND ok=1 "
            "AND title LIKE ? AND created_at >= datetime('now','localtime', ?)",
            (event, f"%{business_key}%", f"-{int(days)} days")).fetchone()
    return bool(row and row["c"] > 0)


def list_logs(limit: int = 50, event: str | None = None) -> list[dict]:
    from db import get_conn
    with get_conn() as conn:
        if event:
            rows = conn.execute(
                "SELECT * FROM notification_logs WHERE event=? ORDER BY id DESC LIMIT ?",
                (event, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notification_logs ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
    return [dict(r) for r in rows]


