# -*- coding: utf-8 -*-
"""收款即入账测试：收款请求 → 顾客确认 → 店主确认入账。

能力边界（测试同样体现）：没有真实资金通道，系统不核验"钱是否真的到账"，
所以必须由店主确认；确认后自动写收入 + 生成借贷凭证。

重点验证：
  - 确认后金额/凭证/合计口径都对
  - 不能重复入账（最危险的错误：重复确认导致多记收入）
  - token 不可猜、且只暴露单笔信息
  - 顾客侧路径免鉴权（否则扫码打不开）

运行：cd server && python -m unittest tests.test_collections -v
"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth
import db

YEAR, MONTH = date.today().year, date.today().month


class TestCollections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "collect.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("payment_collections", "transactions", "vouchers",
                      "voucher_entries", "customers"):
                conn.execute(f"DELETE FROM {t}")

    # ---------- 创建 ----------

    def test_create_collection(self):
        c = db.create_collection(12.5, item="两个肉包一杯豆浆")
        self.assertEqual(c["status"], "pending")
        self.assertEqual(c["amount"], 12.5)
        self.assertTrue(c["token"])
        self.assertGreaterEqual(len(c["token"]), 16, "token 必须够长不可猜")

    def test_create_rejects_bad_amount(self):
        for bad in (0, -5, "abc", None):
            with self.assertRaises(ValueError):
                db.create_collection(bad)
        with self.assertRaises(ValueError):
            db.create_collection(2_000_000)

    def test_tokens_are_unique(self):
        tokens = {db.create_collection(1.0)["token"] for _ in range(30)}
        self.assertEqual(len(tokens), 30, "token 不能重复（否则能打开别人的单子）")

    # ---------- 顾客侧 ----------

    def test_payer_can_mark_paid(self):
        c = db.create_collection(20)
        updated = db.mark_collection_paid(c["token"], payer_name="王阿姨")
        self.assertEqual(updated["status"], "paid")
        self.assertEqual(updated["payer_name"], "王阿姨")
        self.assertTrue(updated["paid_at"])

    def test_mark_paid_is_idempotent(self):
        """顾客手抖点两次不应报错，也不该产生两条记录。"""
        c = db.create_collection(20)
        db.mark_collection_paid(c["token"], "A")
        again = db.mark_collection_paid(c["token"], "A")
        self.assertEqual(again["status"], "paid")
        self.assertEqual(len(db.list_collections()), 1)

    def test_mark_paid_unknown_token(self):
        with self.assertRaises(ValueError):
            db.mark_collection_paid("不存在的token")

    def test_cancelled_cannot_be_paid(self):
        c = db.create_collection(20)
        db.cancel_collection(c["id"], reason="开错了")
        with self.assertRaises(ValueError):
            db.mark_collection_paid(c["token"])

    # ---------- 店主确认入账 ----------

    def test_confirm_creates_income_and_voucher(self):
        """核心：确认后必须真的多一笔收入，且有借贷凭证。"""
        before = db.monthly_summary(YEAR, MONTH)["income"]
        c = db.create_collection(66, item="早点")
        db.mark_collection_paid(c["token"], "李叔")
        r = db.confirm_collection(c["id"])

        self.assertTrue(r["ok"])
        self.assertTrue(r["transaction_id"])
        self.assertTrue(r["voucher"], "必须生成借贷凭证")
        after = db.monthly_summary(YEAR, MONTH)["income"]
        self.assertAlmostEqual(after - before, 66, places=2)
        self.assertIn("66", r["announce"], "应给出可播报的到账文本")

    def test_confirm_voucher_is_balanced(self):
        c = db.create_collection(88, item="收款测试")
        r = db.confirm_collection(c["id"])
        with db.get_conn() as conn:
            entries = [dict(x) for x in conn.execute(
                "SELECT * FROM voucher_entries WHERE voucher_id="
                "(SELECT id FROM vouchers WHERE transaction_id=?)",
                (r["transaction_id"],)).fetchall()]
        self.assertEqual(len(entries), 2)
        d = sum(e["amount"] for e in entries if e["direction"] == "debit")
        cr = sum(e["amount"] for e in entries if e["direction"] == "credit")
        self.assertAlmostEqual(d, cr, places=2)

    def test_confirm_twice_rejected(self):
        """最危险的错误：重复确认会多记一笔收入。"""
        c = db.create_collection(50)
        db.confirm_collection(c["id"])
        with self.assertRaises(ValueError) as cm:
            db.confirm_collection(c["id"])
        self.assertIn("不能重复确认", str(cm.exception))
        # 收入只应增加一次
        self.assertAlmostEqual(db.monthly_summary(YEAR, MONTH)["income"], 50, places=2)

    def test_confirm_from_pending_without_payer_works(self):
        """店主直接确认（顾客没点按钮，比如现金收款）也要能入账。"""
        c = db.create_collection(30)
        r = db.confirm_collection(c["id"])
        self.assertTrue(r["ok"])

    def test_confirm_links_transaction(self):
        c = db.create_collection(10)
        r = db.confirm_collection(c["id"])
        again = db.get_collection(c["id"])
        self.assertEqual(again["status"], "confirmed")
        self.assertEqual(again["transaction_id"], r["transaction_id"])
        self.assertTrue(again["confirmed_at"])

    def test_payer_name_creates_customer(self):
        """顾客填的称呼应自动建熟客档案 —— 收款顺手记住人。"""
        c = db.create_collection(15)
        db.mark_collection_paid(c["token"], "张叔")
        db.confirm_collection(c["id"])
        names = [x["name"] for x in db.list_customers()]
        self.assertIn("张叔", names)

    def test_confirm_rejects_invalid_category(self):
        c = db.create_collection(10)
        with self.assertRaises(ValueError):
            db.confirm_collection(c["id"], category="不存在的分类")

    def test_cancelled_cannot_be_confirmed(self):
        c = db.create_collection(10)
        db.cancel_collection(c["id"])
        with self.assertRaises(ValueError):
            db.confirm_collection(c["id"])

    def test_confirmed_cannot_be_cancelled(self):
        c = db.create_collection(10)
        db.confirm_collection(c["id"])
        with self.assertRaises(ValueError) as cm:
            db.cancel_collection(c["id"])
        self.assertIn("更正", str(cm.exception), "应引导用交易更正")

    # ---------- 列表 ----------

    def test_list_filters_by_status(self):
        a = db.create_collection(1)
        b = db.create_collection(2)
        db.confirm_collection(b["id"])
        self.assertEqual(len(db.list_collections(status="pending")), 1)
        self.assertEqual(len(db.list_collections(status="confirmed")), 1)
        self.assertEqual(len(db.list_collections()), 2)

    def test_list_rejects_bad_status(self):
        with self.assertRaises(ValueError):
            db.list_collections(status="hack")

    # ---------- 鉴权边界 ----------

    def test_pay_paths_are_public(self):
        """顾客扫码必须能打开；否则功能等于不可用。"""
        for p in ("/pay/abc123", "/api/pay/abc123", "/api/pay/abc123/info",
                  "/api/pay/abc123/paid"):
            self.assertTrue(auth.is_public_path(p), f"{p} 应免鉴权")
        # 店主侧仍受保护
        for p in ("/api/collect/create", "/api/collect/list",
                  "/api/collect/1/confirm"):
            self.assertFalse(auth.is_public_path(p), f"{p} 应受保护")

    def test_public_page_does_not_leak_other_data(self):
        """公开接口只暴露单笔金额与状态，不能带出经营数据。"""
        c = db.create_collection(9.9, item="豆浆")
        from routers.collect import pay_info
        info = pay_info(c["token"])
        self.assertEqual(set(info) & {"amount", "item", "status", "shop_name",
                                      "payer_name"},
                         {"amount", "item", "status", "shop_name", "payer_name"})
        for leaked in ("income", "expense", "customers", "note", "customer_id"):
            self.assertNotIn(leaked, info, f"公开接口不该返回 {leaked}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
