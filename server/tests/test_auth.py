# -*- coding: utf-8 -*-
"""访问令牌鉴权测试（auth.py + main.py 中间件接线）

重点覆盖三种部署状态：
  1. 未配置 SHOP_ACCESS_TOKEN → 完全放行（向后兼容，不能破坏既有部署）
  2. 配置了令牌 → /api/** 必须带正确令牌；缺失/错误一律 401
  3. 令牌来源与比较：请求头 / Bearer / 查询参数三条通道，且恒定时间比较

运行：cd server && python -m unittest tests.test_auth -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth
import db

TOKEN = "test-token-abcdefghijklmnop"


class TestAuthHelpers(unittest.TestCase):
    """纯函数层：路径判定与令牌比较"""

    def test_auth_disabled_when_no_token(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": ""}):
            self.assertFalse(auth.auth_enabled())
            self.assertTrue(auth.token_matches("anything"))

    def test_auth_enabled_when_token_set(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            self.assertTrue(auth.auth_enabled())

    def test_token_whitespace_is_trimmed(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": f"  {TOKEN}  "}):
            self.assertTrue(auth.token_matches(TOKEN))

    def test_matches_and_rejects(self):
        self.assertTrue(auth.token_matches(TOKEN, TOKEN))
        self.assertFalse(auth.token_matches("wrong", TOKEN))
        self.assertFalse(auth.token_matches("", TOKEN))
        # 前缀不算匹配（必须完整比较）
        self.assertFalse(auth.token_matches(TOKEN[:-1], TOKEN))

    def test_api_paths_are_protected(self):
        for p in ("/api/orders", "/api/settings", "/api/payment/sources",
                  "/api/report/monthly", "/api/customers"):
            self.assertFalse(auth.is_public_path(p), p)

    def test_page_and_static_paths_are_public(self):
        """页面与静态资源必须放行，否则浏览器加载不出「填写令牌」的界面。"""
        for p in ("/", "/index.html", "/static/js/core.js", "/static/style.css"):
            self.assertTrue(auth.is_public_path(p), p)

    def test_health_is_public(self):
        self.assertTrue(auth.is_public_path("/api/health"))

    def test_generate_token_is_random_and_long(self):
        a, b = auth.generate_token(), auth.generate_token()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 32)


class TestAuthMiddleware(unittest.TestCase):
    """中间件层：用 TestClient 打真实 HTTP"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "auth.db"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _client(self):
        import main
        from fastapi.testclient import TestClient
        return TestClient(main.app)

    def test_disabled_allows_everything(self):
        """未配置令牌：所有接口放行，行为与改造前一致。"""
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": ""}):
            with self._client() as c:
                self.assertEqual(c.get("/api/orders/today").status_code, 200)
                self.assertEqual(c.get("/api/customers").status_code, 200)
                self.assertEqual(c.get("/api/settings").status_code, 200)
                self.assertEqual(c.post("/api/orders", json={"text": "买包子6块"}).status_code, 200)

    def test_enabled_rejects_missing_token(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                r = c.get("/api/orders/today")
                self.assertEqual(r.status_code, 401)
                self.assertIn("访问令牌", r.json()["detail"])
                # 写操作同样拦住
                self.assertEqual(c.post("/api/orders", json={"text": "x"}).status_code, 401)
                self.assertEqual(c.get("/api/settings").status_code, 401)
                self.assertEqual(c.get("/api/payment/sources").status_code, 401)

    def test_enabled_rejects_wrong_token(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                self.assertEqual(
                    c.get("/api/orders/today", headers={"X-Shop-Token": "nope"}).status_code, 401)

    def test_header_token_grants_access(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                r = c.get("/api/orders/today", headers={"X-Shop-Token": TOKEN})
                self.assertEqual(r.status_code, 200)

    def test_bearer_token_grants_access(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                r = c.get("/api/orders/today",
                          headers={"Authorization": f"Bearer {TOKEN}"})
                self.assertEqual(r.status_code, 200)

    def test_query_param_grants_access_for_downloads(self):
        """浏览器直接下载报表无法自定义头部，用 ?token= 兜底。"""
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                r = c.get(f"/api/report/monthly?year=2026&month=9&token={TOKEN}")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.content[:2], b"PK")     # xlsx 压缩包特征

    def test_public_paths_still_reachable(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                self.assertEqual(c.get("/").status_code, 200)
                self.assertEqual(c.get("/api/health").status_code, 200)

    def test_401_carries_cors_header_for_allowed_origin(self):
        """白名单内的 Origin（局域网手机浏览器）必须能读到 401 详情，
        否则前端只会显示笼统的「请求失败」，无法提示填写令牌。"""
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                r = c.get("/api/orders/today",
                          headers={"Origin": "http://192.168.1.5:8000"})
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.headers.get("access-control-allow-origin"),
                                 "http://192.168.1.5:8000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
