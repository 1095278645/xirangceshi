"""notifications.py — 主动触达：多通道消息推送

## 为什么需要

原先所有能力都是**被动**的：店主必须自己想起来打开小程序。而 heartbeat 生成的
每日复盘、reminders 生成的熟客提醒都只躺在数据库里 —— 店主不看就等于不存在，
留存必然极差。这个模块负责把已有内容**主动推出去**。

## 通道与能力边界（必须说清楚）

| 通道 | 能否真正落地 | 说明 |
|---|---|---|
| `mock`（本地落盘） | ✅ 随时可用 | 写到 data/notifications.jsonl，无外部依赖，用于演示与自测 |
| `wecom_bot`（企业微信群机器人） | ✅ 可用 | **不需要正式 AppID**，配一个 webhook key 就能收到真消息 |
| `wecom_app`（企业微信应用消息） | ⚠️ 需资质 | 需要 corpid + secret + agentid；本模块给出适配与配置项 |
| `wechat_subscribe`（微信小程序订阅消息） | ⚠️ 需资质 | 需要正式 AppID + 用户授权 + 平台申请模板；本模块给出适配与配置项 |

微信小程序订阅消息**无法在游客模式端到端演示**（需要正式 AppID、用户逐次授权
订阅、并在公众平台申请模板 ID）。所以这里把"能力"做完整：模板声明、订阅记录、
触发、重试、投递日志、幂等去重；把"最后一跳"抽象成 provider —— 有资质时填上
配置即可切到真实通道。

## 设计

- `PROVIDERS` 注册表：通道 → 发送函数（对标项目里 routers/team_domains 的声明式风格）
- `SUBSCRIPTIONS` 表：谁（通道+目标）订阅了哪些事件
- `NOTIFICATION_LOGS` 表：每次投递的结果（成功/失败/重试次数/错误），可查可追溯
- **幂等**：同一 (事件, 业务键, 日期) 只推一次，避免重启或重复触发刷屏
- **失败重试**：带次数上限，避免坏通道把循环卡住
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

import config

log = logging.getLogger("notifications")

__all__ = [
    "EVENTS", "list_events", "notify", "dispatch_event", "dispatch_event_async",
    "list_subscriptions", "save_subscription", "delete_subscription",
    "list_logs", "recent_mock_messages", "PROVIDER_NAMES",
]

# ---------------- 事件（可订阅的消息类型） ----------------

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
MOCK_FILE = "notifications.jsonl"


def list_events() -> list[dict]:
    return [{"event": k, **v} for k, v in EVENTS.items()]


def _mock_path() -> Path:
    return Path(config.DATA_DIR) / MOCK_FILE


# ---------------- 通道适配（provider） ----------------

def _send_mock(target: str, title: str, content: str) -> dict:
    """本地记录：追加到 data/notifications.jsonl。

    这是**真正可用**的通道（不是占位）：无外部依赖、可查历史，
    也是演示与自测的默认通道。
    """
    path = _mock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "channel": "mock", "target": target or "console",
        "title": title, "content": content,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    log.info("[mock 推送] %s | %s", title, content[:120])
    return {"ok": True, "channel": "mock", "detail": str(path)}


def _post_json(url: str, payload: dict, timeout: int = 10) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"请求失败：{e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def _send_wecom_bot(target: str, title: str, content: str) -> dict:
    """企业微信群机器人。target 为 webhook key（或完整 webhook URL）。

    契约：POST https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=KEY
          body {"msgtype":"markdown","markdown":{"content":"..."}}
          返回 {"errcode":0,...} 表示成功。
    这是无需正式 AppID 即可真正收到消息的通道。
    """
    key = (target or "").strip()
    if not key:
        raise RuntimeError("缺少企业微信机器人 key")
    url = key if key.startswith("http") else (
        f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}")
    text = f"**{title}**\n{content}" if title else content
    resp = _post_json(url, {"msgtype": "markdown", "markdown": {"content": text}})
    if resp.get("errcode") not in (0, None):
        raise RuntimeError(f"企业微信返回错误：{resp}")
    return {"ok": True, "channel": "wecom_bot", "detail": resp}


def _send_webhook(target: str, title: str, content: str, event: str = "") -> dict:
    """自定义 Webhook（开放 API）：把事件以 JSON POST 到 target。

    target 格式：`https://your-host/path`，或 `https://host/path|共享密钥`（带密钥时签名）。
    请求体：`{"event","title","content","at"}`；带密钥时附请求头
    `X-Shopkeeper-Signature: sha256=<hex>`（HMAC-SHA256，接收方可验签防伪造）。
    这让「巷子里的 AI 掌柜」能被 ERP / 代账系统 / 自有看板订阅，形成开放 API 生态。
    """
    raw = (target or "").strip()
    secret = ""
    if "|" in raw:
        raw, secret = raw.split("|", 1)
    url = raw.strip()
    if not url.startswith(("http://", "https://")):
        raise RuntimeError("webhook 地址需以 http:// 或 https:// 开头")
    payload = {
        "event": event or "manual",
        "title": title or "",
        "content": content or "",
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8",
               "X-Shopkeeper-Event": payload["event"]}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Shopkeeper-Signature"] = f"sha256={sig}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", "ignore")[:200]
            code = getattr(resp, "status", 200) or 200
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"webhook HTTP {e.code}: "
                           f"{e.read().decode('utf-8', 'ignore')[:200]}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"webhook 请求失败：{e}") from e
    if code > config.HTTP_SUCCESS_MAX:
        raise RuntimeError(f"webhook 返回 {code}: {text}")
    return {"ok": True, "channel": "webhook", "detail": {"status": code, "body": text}}


def _send_wecom_app(target: str, title: str, content: str) -> dict:
    """企业微信应用消息（需 corpid + secret + agentid，target 为 "corpid:secret:agentid:用户"）。

    相比群机器人是"发给个人"，但要企业微信管理员建应用，配置成本更高。
    """
    parts = (target or "").split(":")
    if len(parts) < 4:
        raise RuntimeError("target 格式应为 corpid:secret:agentid:用户（或 @all）")
    corpid, secret, agentid, touser = parts[0], parts[1], parts[2], parts[3]
    # gettoken 是 GET 接口，不能用 _post_json
    try:
        with urllib.request.urlopen(
                f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}"
                f"&corpsecret={secret}", timeout=10) as resp:
            token_resp = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"获取 access_token 失败：{e}") from e
    token = token_resp.get("access_token")
    if not token:
        raise RuntimeError(f"未取到 access_token：{token_resp}")
    resp = _post_json(
        f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
        {"touser": touser, "msgtype": "text", "agentid": int(agentid),
         "text": {"content": f"{title}\n{content}"}})
    if resp.get("errcode") not in (0, None):
        raise RuntimeError(f"企业微信返回错误：{resp}")
    return {"ok": True, "channel": "wecom_app", "detail": resp}


def _send_wechat_subscribe(target: str, title: str, content: str) -> dict:
    """微信小程序订阅消息适配。

    target 格式："appid:secret:touser_openid:template_id"

    **注意**：这条通道无法在游客模式端到端跑通 —— 需要
      1) 正式 AppID（游客 appid 无法调用）；
      2) 用户在客户端逐次授权订阅（wx.requestSubscribeMessage）；
      3) 在公众平台申请模板并拿到 template_id。
    这里把请求按官方契约构造完整，资质与授权到位即可直接用。
    """
    parts = (target or "").split(":")
    if len(parts) < 4:
        raise RuntimeError(
            "target 格式应为 appid:secret:openid:template_id；"
            "该通道需要正式 AppID 与用户订阅授权")
    appid, secret, openid, template_id = parts[0], parts[1], parts[2], parts[3]
    with urllib.request.urlopen(
            f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential"
            f"&appid={appid}&secret={secret}", timeout=10) as resp:
        token_resp = json.loads(resp.read().decode("utf-8", "ignore"))
    token = token_resp.get("access_token")
    if not token:
        raise RuntimeError(f"未取到 access_token：{token_resp}")
    # 订阅消息的 data 字段需与模板占位符对应；这里用通用两字段，
    # 实际使用时按自己申请的模板调整键名。
    resp = _post_json(
        f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={token}",
        {"touser": openid, "template_id": template_id, "page": "pages/index/index",
         "data": {"thing1": {"value": title[:20]},
                  "thing2": {"value": content[:20]}}})
    if resp.get("errcode") not in (0, None):
        raise RuntimeError(f"微信返回错误：{resp}")
    return {"ok": True, "channel": "wechat_subscribe", "detail": resp}


PROVIDERS = {
    "mock": _send_mock,
    "webhook": _send_webhook,
    "wecom_bot": _send_wecom_bot,
    "wecom_app": _send_wecom_app,
    "wechat_subscribe": _send_wechat_subscribe,
}


def _send_via(channel: str, target: str, title: str, content: str,
              event: str = "") -> dict:
    fn = PROVIDERS.get(channel)
    if not fn:
        raise RuntimeError(f"未知通道：{channel}（可用：{sorted(PROVIDERS)}）")
    if channel == "webhook":       # 开放 API：需要把事件名一并传给接收方
        return fn(target, title, content, event)
    return fn(target, title, content)


# ---------------- 投递（含重试与日志） ----------------

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


def recent_mock_messages(limit: int = 20) -> list[dict]:
    """读取本地记录通道的内容（演示时用它展示"推送到了什么"）。"""
    path = _mock_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))
