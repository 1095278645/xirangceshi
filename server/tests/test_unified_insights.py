# -*- coding: utf-8 -*-
"""统一洞察入口：同日缓存、强制刷新、失败本地兜底。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import insight_service
from routers.basic import router


class _TempDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "unified-insights.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM domain_context")


class TestUnifiedInsightService(_TempDB):
    def test_cache_and_refresh(self):
        calls = []

        def handler(payload):
            calls.append(payload)
            return {"text": "第一条"}

        with mock.patch.dict(insight_service._HANDLERS, {"copy": handler}):
            first = insight_service.generate("copy", {"scene": "开业"})
            cached = insight_service.generate("copy", {"scene": "开业"})
            refreshed = insight_service.generate("copy", {"scene": "开业"}, refresh=True)
        self.assertFalse(first["cached"])
        self.assertTrue(cached["cached"])
        self.assertFalse(refreshed["cached"])
        self.assertEqual(len(calls), 2)

    def test_failure_returns_friendly_fallback(self):
        def handler(_payload):
            raise RuntimeError("APIConnectionError: secret detail")

        with mock.patch.dict(insight_service._HANDLERS, {"copy": handler}):
            result = insight_service.generate("copy", {"scene": "开业"})
        self.assertTrue(result["degraded"])
        self.assertNotIn("APIConnectionError", result["message"])
        self.assertIn("AI 服务暂时不可用", result["message"])

    def test_unknown_scene_rejected(self):
        with self.assertRaises(ValueError):
            insight_service.generate("unknown", {})


class TestUnifiedInsightAPI(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_unknown_scene_returns_400(self):
        response = self.client.post("/api/insights", json={"scene": "unknown", "payload": {}})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
