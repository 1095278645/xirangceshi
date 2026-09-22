# -*- coding: utf-8 -*-
"""开放 API / Webhook 测试：JSON 契约 + HMAC 签名 + 订阅分发 + 失败重试。

为什么用本地 HTTP 服务器：要验证"真的 POST 出去了什么"（body/签名头），
而不是只测"函数不报错"—— 这正是对外契约。

运行：cd server && python -m unittest tests.test_webhook -v
"""
import hashlib
import hmac
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import notifications


class _Handler(BaseHTTPRequestHandler):
    received: list = []
    status = 200

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).received.append({"headers": dict(self.headers), "body": body,
                                    "path": self.path})
        self.send_response(type(self).status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # 静音
        pass


class _Server:
    def __enter__(self):
        _Handler.received = []
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/hook"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestWebhookProvider(unittest.TestCase):
    def test_payload_and_signature(self):
        with _Server() as srv:
            target = f"{srv.url}|secret123"
            r = notifications._send_webhook(target, "记账成功", "收入 6 元",
                                            event="order_created")
            self.assertTrue(r["ok"])
            req = _Handler.received[-1]
            payload = json.loads(req["body"].decode("utf-8"))
            self.assertEqual(payload["event"], "order_created")
            self.assertEqual(payload["title"], "记账成功")
            self.assertIn("at", payload)
            self.assertEqual(req["headers"].get("X-Shopkeeper-Event"), "order_created")
            expected = "sha256=" + hmac.new(b"secret123", req["body"],
                                            hashlib.sha256).hexdigest()
            self.assertEqual(req["headers"].get("X-Shopkeeper-Signature"), expected)

    def test_without_secret_has_no_signature(self):
        with _Server() as srv:
            notifications._send_webhook(srv.url, "t", "c", event="manual")
            req = _Handler.received[-1]
            self.assertNotIn("X-Shopkeeper-Signature", req["headers"])

    def test_rejects_non_http_target(self):
        with self.assertRaises(RuntimeError):
            notifications._send_webhook("ftp://x", "t", "c")

    def test_event_registered(self):
        self.assertIn("order_created", notifications.EVENTS)
        self.assertIn("webhook", notifications.PROVIDERS)


class TestWebhookDispatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "hook.db"
        db._schema_ready.clear()
        self.addCleanup(db._schema_ready.clear)
        db.init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def test_dispatch_to_webhook_subscriber(self):
        with _Server() as srv:
            notifications.save_subscription("webhook", srv.url, ["order_created"],
                                            name="测试 webhook")
            r = notifications.dispatch_event("order_created", "记账成功", "收入 6 元",
                                             business_key="txn-1")
            self.assertTrue(r["ok"])
            self.assertFalse(r["skipped"])
            self.assertTrue(r["results"][0]["ok"])
            self.assertEqual(len(_Handler.received), 1)

    def test_failed_webhook_logged(self):
        s = notifications.save_subscription("webhook", "http://127.0.0.1:1/nope",
                                            ["order_created"])
        r = notifications.dispatch_event("order_created", "t", "c", business_key="txn-2")
        self.assertFalse(r["ok"])
        logs = notifications.list_logs(limit=10, event="order_created")
        self.assertTrue(any(l["channel"] == "webhook" and l["ok"] == 0 for l in logs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
