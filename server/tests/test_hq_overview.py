# -*- coding: utf-8 -*-
"""连锁总部视图：跨店汇总必须"一店一数、绝不混库"。

运行：cd server && python -m unittest tests.test_hq_overview -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
import shops


class TestHQOverview(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig = (config.DATA_DIR, config.DB_PATH,
                      shops.SHOPS_DIR, shops.REGISTRY_PATH)
        config.DATA_DIR = str(root)
        config.DB_PATH = str(root / "ai_shopkeeper.db")
        shops.SHOPS_DIR = root / "shops"
        shops.REGISTRY_PATH = root / "registry.db"
        db.DB_PATH = Path(config.DB_PATH)
        db._schema_ready.clear()
        shops.init_registry()

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, shops.SHOPS_DIR, shops.REGISTRY_PATH = self._orig
        db.DB_PATH = Path(config.DB_PATH)
        db._schema_ready.clear()
        self._tmp.cleanup()

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import shops as shops_router
        app = FastAPI()
        app.include_router(shops_router.router)
        return TestClient(app)

    def _new_shop(self, name):
        shops.create_shop(name)
        return next(s["id"] for s in shops.list_shops() if s["name"] == name)

    def test_overview_aggregates_per_shop_without_mixing(self):
        first = shops.list_shops()[0]["id"]
        second = self._new_shop("二号店")

        with shops.use_shop(first):
            db.find_or_create_customer("甲店客")
            db.add_transaction(None, "包子", 100, trans_type="income",
                               category="主营业务收入")
        with shops.use_shop(second):
            db.find_or_create_customer("乙店客")
            db.add_transaction(None, "豆浆", 50, trans_type="income",
                               category="主营业务收入")
            db.add_transaction(None, "豆子", 20, trans_type="expense",
                               category="进货")

        body = self._client().get("/api/shops/overview").json()
        self.assertEqual(len(body["shops"]), 2)
        by_id = {r["shop_id"]: r for r in body["shops"]}
        self.assertEqual(by_id[first]["income"], 100)          # 一店一数，未混库
        self.assertEqual(by_id[first]["expense"], 0)
        self.assertEqual(by_id[second]["income"], 50)
        self.assertEqual(by_id[second]["expense"], 20)
        self.assertEqual(body["total"]["income"], 150)
        self.assertEqual(body["total"]["balance"], 130)
        self.assertEqual(body["total"]["shops"], 2)

    def test_overview_handles_empty_shop(self):
        shops.create_shop("空店")
        body = self._client().get("/api/shops/overview").json()
        self.assertEqual(body["total"]["income"], 0)
        self.assertEqual(len(body["shops"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
