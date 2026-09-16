# -*- coding: utf-8 -*-
"""收款流程的 HTTP 层回归测试（含鉴权边界）。

为什么要有这一层：db_collections 的测试只覆盖数据层，但**顾客扫码能不能打开
公开页**、**店主侧是否真的受保护**都是路由/中间件行为，只有走 HTTP 才测得到。
公开收款页若被鉴权拦掉，功能等于不可用；反过来店主侧若被放行，等于账本裸奔。

运行：cd server && python -m unittest tests.test_collect_http -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

TOKEN = "unit-test-shop-token"


class TestCollectHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "collect_http.db"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("payment_collections", "transactions", "vouchers",
                      "voucher_entries", "customers"):
                conn.execute(f"DELETE FROM {t}")

    def _client(self):
        import main
        from fastapi.testclient import TestClient
        return TestClient(main.app)

    # ---------- 鉴权开启时的边界 ----------

    def test_public_pay_page_reachable_without_token(self):
        """核心：顾客手机没有令牌，公开页必须能打开。"""
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                created = c.post("/api/collect/create",
                                 json={"amount": 12, "item": "豆浆"},
                                 headers={"X-Shop-Token": TOKEN}).json()
                token = created["collection"]["token"]

                # 顾客侧：不带令牌
                page = c.get(f"/pay/{token}")
                self.assertEqual(page.status_code, 200, "公开收款页不该被鉴权拦")
                self.assertIn("<!DOCTYPE html>", page.text)

                info = c.get(f"/api/pay/{token}/info")
                self.assertEqual(info.status_code, 200)
                self.assertEqual(info.json()["amount"], 12)

                paid = c.post(f"/api/pay/{token}/paid", json={"payer_name": "王阿姨"})
                self.assertEqual(paid.status_code, 200)
                self.assertEqual(paid.json()["status"], "paid")

    def test_owner_endpoints_require_token(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                self.assertEqual(c.get("/api/collect/list").status_code, 401)
                self.assertEqual(c.post("/api/collect/create",
                                        json={"amount": 1}).status_code, 401)
                self.assertEqual(c.post("/api/collect/1/confirm").status_code, 401)

    def test_public_info_does_not_leak_business_data(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                token = c.post("/api/collect/create", json={"amount": 5},
                               headers={"X-Shop-Token": TOKEN}).json()["collection"]["token"]
                info = c.get(f"/api/pay/{token}/info").json()
        for leaked in ("income", "expense", "customer_id", "note", "customers"):
            self.assertNotIn(leaked, info, f"公开接口不该返回 {leaked}")

    def test_unknown_token_404(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": TOKEN}):
            with self._client() as c:
                self.assertEqual(c.get("/api/pay/nosuchtoken/info").status_code, 404)

    # ---------- 完整链路（鉴权关闭，走默认部署） ----------

    def test_full_flow_create_pay_confirm(self):
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": ""}):
            with self._client() as c:
                before = c.get("/api/orders/monthly").json()["income"]

                created = c.post("/api/collect/create",
                                 json={"amount": 25.5, "item": "早点"}).json()
                cid = created["collection"]["id"]
                token = created["collection"]["token"]
                self.assertTrue(created["pay_url"].endswith(token))

                self.assertEqual(c.post(f"/api/pay/{token}/paid",
                                        json={"payer_name": "李叔"}).status_code, 200)

                lst = c.get("/api/collect/list?status=paid").json()
                self.assertEqual(lst["pending_count"], 1)

                conf = c.post(f"/api/collect/{cid}/confirm").json()
                self.assertTrue(conf["transaction_id"])
                self.assertEqual(conf["voucher"]["credit"], "主营业务收入")
                self.assertIn("25.5", conf["announce"])

                after = c.get("/api/orders/monthly").json()["income"]
                self.assertAlmostEqual(after - before, 25.5, places=2)

                # 幂等：不能重复入账
                again = c.post(f"/api/collect/{cid}/confirm")
                self.assertEqual(again.status_code, 400)

    def test_confirm_after_payer_name_creates_customer(self):
        """顾客填的称呼应自动建熟客档案 —— 收款顺手记住人。"""
        with mock.patch.dict(os.environ, {"SHOP_ACCESS_TOKEN": ""}):
            with self._client() as c:
                token = c.post("/api/collect/create",
                               json={"amount": 9}).json()["collection"]["token"]
                c.post(f"/api/pay/{token}/paid", json={"payer_name": "张叔"})
                cid = c.get("/api/collect/list").json()["collections"][0]["id"]
                c.post(f"/api/collect/{cid}/confirm")

                payload = c.get("/api/customers").json()
                rows = payload.get("customers", payload) if isinstance(payload, dict) else payload
                names = [x["name"] for x in rows]
        self.assertIn("张叔", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
