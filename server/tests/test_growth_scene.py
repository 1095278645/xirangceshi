# -*- coding: utf-8 -*-
"""P1-4 增长动作（拉新/复购/选品）：场景可用 + 无 Key 规则兜底。

运行：cd server && python -m unittest tests.test_growth_scene -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai
import db
import insight_service


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "growth.db"
        db._schema_ready.clear()
        db.init_db()
        self.addCleanup(db._schema_ready.clear)

    def tearDown(self):
        self._tmp.cleanup()


class TestGrowthFallback(_Base):
    def test_rule_based_three_actions_without_key(self):
        cid, _ = db.find_or_create_customer("王阿姨")
        db.add_transaction(cid, "肉包", 6, trans_type="income",
                           category="主营业务收入", counterparty="王阿姨")
        db.add_product("豆浆", unit="杯", stock_qty=10, unit_cost=1.5)
        with mock.patch.object(ai, "ai_available", return_value=False):
            r = insight_service.generate("growth", {})
        self.assertFalse(r["ai_used"])
        acts = r["actions"]
        for mark in ("①", "②", "③"):
            self.assertIn(mark, acts, f"应包含{mark}动作")
        self.assertIn("豆浆", acts)          # 选品动作点名了库存里滞销/低值品

    def test_unknown_scene_still_rejected(self):
        with self.assertRaises(ValueError):
            insight_service.generate("nope", {})


class TestGrowthHTTP(_Base):
    def test_insights_accepts_growth_scene(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import basic
        app = FastAPI()
        app.include_router(basic.router)
        c = TestClient(app)
        with mock.patch.object(ai, "ai_available", return_value=False):
            r = c.post("/api/insights", json={"scene": "growth", "payload": {}})
        self.assertEqual(r.status_code, 200)
        self.assertIn("actions", r.json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
