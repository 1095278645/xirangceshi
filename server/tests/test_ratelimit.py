# -*- coding: utf-8 -*-
"""接口限流测试：滑动窗口限流器 + 规则映射 + 中间件真实拦截。

背景：`/pay/*`、`/api/pay/*`、`/api/voice/transcribe` 免鉴权，
一旦暴露到公网可被刷（语音上传尤其重）。这里验证限流真的生效。

运行：cd server && python -m unittest tests.test_ratelimit -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ratelimit


class TestSlidingWindow(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        lim = ratelimit.SlidingWindowLimiter(3, window_seconds=60)
        for i in range(3):
            self.assertTrue(lim.allow("k", 1000.0 + i * 0.1))
        self.assertFalse(lim.allow("k", 1000.5))

    def test_window_slides(self):
        lim = ratelimit.SlidingWindowLimiter(2, window_seconds=10)
        self.assertTrue(lim.allow("k", 0.0))
        self.assertTrue(lim.allow("k", 1.0))
        self.assertFalse(lim.allow("k", 2.0))
        # 最早的命中（t=0）滑出窗口后放行
        self.assertTrue(lim.allow("k", 10.5))

    def test_keys_are_isolated(self):
        lim = ratelimit.SlidingWindowLimiter(1, window_seconds=60)
        self.assertTrue(lim.allow("a", 0.0))
        self.assertTrue(lim.allow("b", 0.0))
        self.assertFalse(lim.allow("a", 0.0))

    def test_retry_after(self):
        lim = ratelimit.SlidingWindowLimiter(1, window_seconds=60)
        lim.allow("k", 0.0)
        self.assertGreater(lim.retry_after("k", 10.0), 0)
        self.assertEqual(lim.retry_after("nope", 10.0), 0)


class TestRules(unittest.TestCase):
    def test_rule_mapping(self):
        self.assertIsNone(ratelimit.rule_for("/api/health"))
        self.assertEqual(ratelimit.rule_for("/api/voice/transcribe"), "voice")
        self.assertEqual(ratelimit.rule_for("/api/pay/abc/info"), "public_pay")
        self.assertEqual(ratelimit.rule_for("/pay/abc"), "public_pay")
        self.assertEqual(ratelimit.rule_for("/api/orders"), "api")
        self.assertIsNone(ratelimit.rule_for("/static/js/core.js"))


class TestMiddleware(unittest.TestCase):
    def _client(self, per_min=2, voice_per_min=1):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.add_middleware(ratelimit.RateLimitMiddleware,
                           per_min=per_min, voice_per_min=voice_per_min)

        @app.get("/api/ping")
        def ping():
            return {"ok": True}

        @app.get("/api/health")
        def health():
            return {"ok": True}

        return TestClient(app)

    def test_enforces_limit_when_not_test_env(self):
        c = self._client(per_min=2)
        with mock.patch.object(ratelimit, "_is_test_env", lambda: False):
            self.assertEqual(c.get("/api/ping").status_code, 200)
            self.assertEqual(c.get("/api/ping").status_code, 200)
            r = c.get("/api/ping")
            self.assertEqual(r.status_code, 429)
            self.assertIn("Retry-After", r.headers)

    def test_health_always_exempt(self):
        c = self._client(per_min=1)
        with mock.patch.object(ratelimit, "_is_test_env", lambda: False):
            for _ in range(5):
                self.assertEqual(c.get("/api/health").status_code, 200)

    def test_disabled_by_env(self):
        c = self._client(per_min=1)
        with mock.patch.object(ratelimit, "_is_test_env", lambda: False), \
                mock.patch.dict("os.environ", {"SHOP_RATE_LIMIT": "0"}):
            for _ in range(5):
                self.assertEqual(c.get("/api/ping").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
