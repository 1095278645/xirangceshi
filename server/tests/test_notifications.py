# -*- coding: utf-8 -*-
"""主动触达测试：多通道推送 / 重试 / 订阅分发 / 幂等去重 / 投递日志。

为什么这么测：
  - mock 通道是演示与自测的默认通道，必须真的落盘可查；
  - **企业微信机器人是唯一能真正落地（无需正式 AppID）的通道**，
    所以用本地 HTTP 服务器验证它构造的请求体契约（msgtype/markdown/content），
    而不是只测"函数不报错"；
  - 微信订阅消息需要资质，无法端到端跑通，但配置缺失时必须给出**明确**错误，
    否则用户会以为"配好了却不发"；
  - 幂等与重试是防止刷屏/卡死的护栏。

运行：cd server && python -m unittest tests.test_notifications -v
"""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
import notifications


class _FakeHandler(BaseHTTPRequestHandler):
    """记录收到的请求，按预设返回。"""
    received: list = []
    reply: dict = {"errcode": 0, "errmsg": "ok"}
    status = 200

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        type(self).received.append({"path": self.path, "body": body})
        payload = json.dumps(type(self).reply).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静音
        pass


class _FakeServer:
    def __enter__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _FakeHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestNotifications(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        db.DB_PATH = cls.root / "notify.db"
        cls._patches = [mock.patch.object(config, "DATA_DIR", cls.root)]
        for p in cls._patches:
            p.start()
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM notification_subscriptions")
            conn.execute("DELETE FROM notification_logs")
        mock_file = Path(config.DATA_DIR) / notifications.MOCK_FILE
        if mock_file.exists():
            mock_file.unlink()
        _FakeHandler.received = []
        _FakeHandler.reply = {"errcode": 0, "errmsg": "ok"}
        _FakeHandler.status = 200

    # ---------- mock 通道 ----------

    def test_mock_channel_writes_and_reads_back(self):
        r = notifications.notify("mock", "", "标题", "正文")
        self.assertTrue(r["ok"])
        msgs = notifications.recent_mock_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["title"], "标题")
        self.assertEqual(msgs[0]["content"], "正文")

    def test_mock_messages_newest_first(self):
        notifications.notify("mock", "", "第一条", "a")
        notifications.notify("mock", "", "第二条", "b")
        msgs = notifications.recent_mock_messages()
        self.assertEqual([m["title"] for m in msgs], ["第二条", "第一条"])

    # ---------- 企业微信机器人（可用真实通道） ----------

    def test_wecom_bot_request_contract(self):
        """验证请求体契约：msgtype=markdown，content 含标题与正文。"""
        with _FakeServer() as srv:
            r = notifications.notify("wecom_bot", f"{srv.url}/cgi-bin/webhook/send?key=K",
                                     "今日复盘", "收 100 元")
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(_FakeHandler.received), 1)
        sent = json.loads(_FakeHandler.received[0]["body"])
        self.assertEqual(sent["msgtype"], "markdown")
        self.assertIn("今日复盘", sent["markdown"]["content"])
        self.assertIn("收 100 元", sent["markdown"]["content"])

    def test_wecom_bot_accepts_bare_key_and_builds_official_url(self):
        """用户通常只粘贴 key，要能自动拼官方地址。"""
        captured = {}

        def fake_post(url, payload, timeout=10):
            captured["url"] = url
            captured["payload"] = payload
            return {"errcode": 0}

        with mock.patch.object(notifications, "_post_json", fake_post):
            r = notifications.notify("wecom_bot", "MYKEY123", "T", "C")
        self.assertTrue(r["ok"])
        self.assertIn("qyapi.weixin.qq.com/cgi-bin/webhook/send", captured["url"])
        self.assertIn("key=MYKEY123", captured["url"])

    def test_wecom_bot_error_code_is_failure(self):
        """企业微信用 errcode 表达失败，不能只看 HTTP 200。"""
        _FakeHandler.reply = {"errcode": 93000, "errmsg": "invalid webhook url"}
        with _FakeServer() as srv:
            r = notifications.notify("wecom_bot", f"{srv.url}/x", "T", "C", retries=1)
        self.assertFalse(r["ok"])
        self.assertIn("93000", r["error"])

    def test_wecom_bot_requires_key(self):
        r = notifications.notify("wecom_bot", "", "T", "C", retries=1)
        self.assertFalse(r["ok"])
        self.assertIn("key", r["error"])

    # ---------- 重试与日志 ----------

    def test_failure_retries_then_logs(self):
        calls = {"n": 0}

        def fail(*a, **k):
            calls["n"] += 1
            raise RuntimeError("通道坏了")

        with mock.patch.dict(notifications.PROVIDERS, {"boom": fail}):
            r = notifications.notify("boom", "t", "T", "C", event="manual", retries=3)
        self.assertFalse(r["ok"])
        self.assertEqual(calls["n"], 3, "应按 retries 次数重试")
        self.assertEqual(r["attempts"], 3)

        logs = notifications.list_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["ok"], 0)
        self.assertEqual(logs[0]["attempts"], 3)
        self.assertIn("通道坏了", logs[0]["error"])

    def test_success_logged_once(self):
        notifications.notify("mock", "", "T", "C")
        logs = notifications.list_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["ok"], 1)
        self.assertEqual(logs[0]["attempts"], 1)

    def test_unknown_channel_rejected(self):
        r = notifications.notify("nope", "t", "T", "C", retries=1)
        self.assertFalse(r["ok"])
        self.assertIn("未知通道", r["error"])

    # ---------- 订阅 ----------

    def test_save_and_list_subscription(self):
        s = notifications.save_subscription("mock", "", ["daily_review"], name="本机")
        self.assertTrue(s["id"])
        subs = notifications.list_subscriptions()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["events"], ["daily_review"])

    def test_subscription_rejects_unknown_channel_and_event(self):
        with self.assertRaises(ValueError):
            notifications.save_subscription("nope", "", [])
        with self.assertRaises(ValueError):
            notifications.save_subscription("mock", "", ["不存在的"])

    def test_non_mock_channel_requires_target(self):
        with self.assertRaises(ValueError) as cm:
            notifications.save_subscription("wecom_bot", "", [])
        self.assertIn("需要填写接收目标", str(cm.exception))

    def test_delete_subscription(self):
        s = notifications.save_subscription("mock", "", [])
        self.assertTrue(notifications.delete_subscription(s["id"]))
        self.assertFalse(notifications.delete_subscription(s["id"]))

    # ---------- 分发 ----------

    def test_dispatch_sends_to_matching_subscribers_only(self):
        notifications.save_subscription("mock", "", ["daily_review"], name="只要复盘")
        notifications.save_subscription("mock", "", ["payment_received"], name="只要收款")
        r = notifications.dispatch_event("daily_review", "复盘", "内容")
        self.assertEqual(len(r["results"]), 1)
        self.assertEqual(len(notifications.recent_mock_messages()), 1)

    def test_empty_events_means_subscribe_all(self):
        notifications.save_subscription("mock", "", [], name="全订")
        r = notifications.dispatch_event("payment_received", "收款", "6 元")
        self.assertEqual(len(r["results"]), 1)

    def test_disabled_subscription_skipped(self):
        notifications.save_subscription("mock", "", [], enabled=False)
        r = notifications.dispatch_event("daily_review", "复盘", "内容")
        self.assertTrue(r["skipped"])
        self.assertEqual(r["reason"], "no_subscriber")

    def test_dispatch_rejects_unknown_event(self):
        with self.assertRaises(ValueError):
            notifications.dispatch_event("nope", "T", "C")

    # ---------- 幂等去重 ----------

    def test_dedup_same_business_key_within_window(self):
        """服务重启/重复触发不应刷屏：同一业务键当天只推一次。"""
        notifications.save_subscription("mock", "", ["daily_review"])
        first = notifications.dispatch_event("daily_review", "复盘 2026-09-16", "内容",
                                            business_key="2026-09-16", dedup_days=1)
        self.assertFalse(first["skipped"])
        second = notifications.dispatch_event("daily_review", "复盘 2026-09-16", "内容",
                                             business_key="2026-09-16", dedup_days=1)
        self.assertTrue(second["skipped"])
        self.assertEqual(second["reason"], "dedup")
        self.assertEqual(len(notifications.recent_mock_messages()), 1, "只应推送一次")

    def test_dedup_zero_days_always_sends(self):
        notifications.save_subscription("mock", "", ["daily_review"])
        notifications.dispatch_event("daily_review", "T", "C", business_key="k", dedup_days=0)
        r = notifications.dispatch_event("daily_review", "T", "C", business_key="k", dedup_days=0)
        self.assertFalse(r["skipped"])

    def test_failed_delivery_does_not_block_retry_next_time(self):
        """失败不该被当成"已推送"而永久跳过。"""
        notifications.save_subscription("mock", "", ["daily_review"])
        # 注意：PROVIDERS 在模块导入时就绑定了函数对象，patch 模块属性不生效，
        # 必须 patch PROVIDERS 本身（这是同类"早绑定"坑，测试里也踩了一次）。
        def boom(*a, **k):
            raise RuntimeError("坏了")

        with mock.patch.dict(notifications.PROVIDERS, {"mock": boom}):
            first = notifications.dispatch_event(
                "daily_review", "复盘 k", "C", business_key="k", dedup_days=1)
        self.assertFalse(first["ok"], "第一次应失败")
        r = notifications.dispatch_event("daily_review", "复盘 k", "C",
                                         business_key="k", dedup_days=1)
        self.assertFalse(r["skipped"], "上次失败过，这次应重试而不是跳过")

    # ---------- 需要资质的通道要给明确提示 ----------

    def test_wechat_subscribe_requires_formal_appid_config(self):
        r = notifications.notify("wechat_subscribe", "", "T", "C", retries=1)
        self.assertFalse(r["ok"])
        self.assertIn("正式 AppID", r["error"])

    def test_wecom_app_requires_full_target(self):
        r = notifications.notify("wecom_app", "corpid:secret", "T", "C", retries=1)
        self.assertFalse(r["ok"])
        self.assertIn("格式", r["error"])

    # ---------- 事件声明 ----------

    def test_events_and_providers_declared(self):
        events = {e["event"] for e in notifications.list_events()}
        self.assertEqual(events, {"daily_review", "customer_reminder",
                                  "payment_received", "revenue_warning"})
        for e in notifications.list_events():
            self.assertTrue(e["name"] and e["desc"] and e["trigger"])
        for ch in ("mock", "wecom_bot", "wecom_app", "wechat_subscribe"):
            self.assertIn(ch, notifications.PROVIDERS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
