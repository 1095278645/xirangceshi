"""notify_channels.py — 触达通道适配（从 notifications.py 外移，架构自检 L1）

职责：把"通道"具体实现集中在一处 —— mock / webhook / 企业微信机器人 / 企业微信应用 /
小程序订阅消息，以及 `PROVIDERS` 注册表与 `_send_via` 分发、本地收件箱读取。

依赖方向：本模块**不依赖 notifications**（避免成环）；notifications 反向 re-export
本模块的 `PROVIDERS/_send_via/recent_mock_messages/_mock_path/MOCK_FILE`，对外接口不变。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger("notify_channels")

MOCK_FILE = "notifications.jsonl"


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
