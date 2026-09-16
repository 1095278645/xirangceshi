# -*- coding: utf-8 -*-
"""tests/test_order_amount_missing.py — 「一句话记账」的最后一环

## 为什么单独一个文件

用真实模型实测 20 条店主原话后发现：解析本身是可靠的（金额 20/20、熟客 20/20、
方向 19/20、分类 18/20），真正会让店主**吃亏**的是这一环 ——

  1. 金额没听懂时，旧实现照样往库里插一行 amount=0 的流水，只弹一句
     "金额没听清，只记了流水"。结果：一条既不进合计、又不会消失的幽灵记录
     躺在流水里，店主当天不会发现，月底对不上账也不知道从哪来的。
  2. 分类解析成非标准名（实测模型返回过"工资"）时，旧实现静默兜底到办公费，
     账本品类与凭证科目对不上，店主看不出来。

这里把这两件事的正确行为钉死。AI 调用全部打桩，不联网、不耗额度。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import categories
import config
import db


class OrderAmountMissingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        config.DB_PATH = str(Path(cls._tmp.name) / "missing.db")
        db.DB_PATH = Path(config.DB_PATH)
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("transactions", "vouchers", "voucher_entries",
                      "customers", "memories"):
                conn.execute(f"DELETE FROM {t}")

    def _client(self):
        import main
        from fastapi.testclient import TestClient
        return TestClient(main.app)

    def _parse_stub(self, **over):
        base = {"customer": "", "item": "包子", "amount": None, "note": "",
                "tags": "", "category": "主营业务收入", "trans_type": "income"}
        base.update(over)
        return base

    # ---------- 1. 金额没听懂：不能落库 ----------

    def test_missing_amount_does_not_write_ghost_row(self):
        """金额没听懂时，绝不能往流水里插 0 元幽灵记录。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._parse_stub(customer="张叔", item="包子")):
            with self._client() as c:
                r = c.post("/api/orders", json={"text": "张叔拿了个包子"})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertTrue(j["amount_missing"], "必须把『没听懂金额』这件事报出来")

        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            zero = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE amount=0").fetchone()[0]
        self.assertEqual(n, 0, "金额没听懂就不该有流水记录")
        self.assertEqual(zero, 0, "不能留下 0 元记录")

    def test_missing_amount_reports_draft_fields_for_followup(self):
        """要返回"草稿"信息，界面才能追问"这笔多少钱"并把其它字段续上。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._parse_stub(customer="张叔", item="包子",
                                                      category="主营业务收入")):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "张叔拿了个包子"}).json()
        self.assertTrue(j["amount_missing"])
        draft = j.get("draft") or {}
        self.assertEqual(draft.get("item"), "包子", "草稿要带上事由")
        self.assertEqual(draft.get("customer"), "张叔", "草稿要带上熟客")
        self.assertEqual(draft.get("trans_type"), "income", "草稿要带上方向")
        self.assertIsNone(j.get("order_id"),
                          "没有落库就不该返回 order_id（前端会误以为记好了）")

    def test_missing_amount_does_not_bump_today_count(self):
        """今日笔数不能把"没记成"的算进去，否则首页数字当场就对不上。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._parse_stub(customer="张叔")):
            with self._client() as c:
                before = c.get("/api/orders/today").json()["cnt"]
                c.post("/api/orders", json={"text": "张叔拿了个包子"})
                after = c.get("/api/orders/today").json()["cnt"]
        self.assertEqual(before, after, "没记成的一笔不该让笔数 +1")

    def test_followup_with_amount_records_normally(self):
        """补上金额后再记一次，应该正常落库并生成凭证。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._parse_stub(customer="张叔", item="包子")):
            with self._client() as c:
                draft = c.post("/api/orders", json={"text": "张叔拿了个包子"}).json()
                # 界面把草稿原样回填，用户补金额（customer/item 由前端带上）
                again = c.post("/api/orders", json={
                    "text": "张叔拿了包子", "amount": 6,
                    "customer": draft["draft"]["customer"]}).json()
        self.assertFalse(again["amount_missing"])
        self.assertTrue(again["order_id"], "补了金额就该真的记下来")
        self.assertTrue(again["voucher"], "有金额就该生成借贷凭证")
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT amount, item, customer_id FROM transactions").fetchone()
        self.assertAlmostEqual(row["amount"], 6.0, places=2)

    def test_followup_with_explicit_fields_does_not_recall_ai(self):
        """带上 item+amount 补记时，不该再调一次 AI。

        两个原因：省一次调用；更重要的是**不重解析就不会漂移** ——
        重解析可能把科目或熟客理解成别的，补记的账就和第一次的草稿对不上了。
        """
        calls = []

        def _spy(text):
            calls.append(text)
            return self._parse_stub(customer="张叔", item="包子")

        with mock.patch("ai.parse_transaction", side_effect=_spy):
            with self._client() as c:
                c.post("/api/orders", json={"text": "张叔拿了个包子"})
                self.assertEqual(len(calls), 1, "第一次要解析")
                r = c.post("/api/orders", json={
                    "text": "张叔拿了包子", "amount": 6, "customer": "张叔",
                    "item": "包子", "category": "主营业务收入",
                    "trans_type": "income"})
        self.assertEqual(len(calls), 1, "补记不该再调 AI")
        j = r.json()
        self.assertEqual(j["recorded"]["item"], "包子")
        self.assertEqual(j["recorded"]["category"], "主营业务收入")

    def test_recorded_block_reports_what_was_booked(self):
        """成功时要回一份"到底记成了什么"，界面才能摆出来给店主核对。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._parse_stub(amount=6, customer="张叔",
                                                      item="两个肉包")):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "张叔买了两个肉包6块"}).json()
        rec = j.get("recorded") or {}
        self.assertEqual(rec.get("amount"), 6)
        self.assertEqual(rec.get("trans_type"), "income")
        self.assertEqual(rec.get("category"), "主营业务收入")
        self.assertEqual(rec.get("customer"), "张叔")
        self.assertTrue(rec.get("transaction_id"), "要能给『就地改』提供交易 id")

    def test_alias_category_is_normalized_and_flagged(self):
        """模型返回近义分类（如"工资"）时，要归一到标准名并标记出来。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._parse_stub(amount=3500, category="工资",
                                                      trans_type="expense",
                                                      item="发工资")):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "给小工发了3500工资"}).json()
        rec = j.get("recorded") or {}
        self.assertEqual(rec.get("category"), "职工薪酬",
                         "「工资」应归一到标准科目，而不是静默兜底到办公费")
        self.assertTrue(rec.get("category_normalized"), "归一过要标记，便于界面提示")
        self.assertEqual(rec.get("raw_category"), "工资")
        # 凭证也必须记到职工薪酬对应的科目上
        with db.get_conn() as conn:
            acc = conn.execute(
                "SELECT account_code FROM voucher_entries WHERE direction='debit'"
            ).fetchone()[0]
        self.assertEqual(acc, "560106", "应付职工薪酬对应的借方科目")

    def test_zero_amount_explicitly_is_still_rejected(self):
        """显式传 0 也要拦住 —— 0 元记账在会计上没有意义。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._parse_stub()):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "随便记一笔",
                                                "amount": 0}).json()
        self.assertTrue(j["amount_missing"])
        with db.get_conn() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)


class CategoryNormalizeTest(unittest.TestCase):
    """模型偶尔返回非标准分类名（实测返回过"工资"），必须归一到标准名。"""

    def test_alias_maps_to_canonical(self):
        cases = {
            "工资": "职工薪酬",
            "房租": "租赁及物业费",
            "水电费": "租赁及物业费",
            "进货": "进货",
            "主营业务收入": "主营业务收入",
        }
        for given, want in cases.items():
            with self.subTest(given=given):
                self.assertEqual(categories.normalize_category(given), want)

    def test_unknown_is_not_silently_mapped(self):
        """认不出来的返回空串，让调用方显式决定怎么兜底（不要静默猜）。"""
        self.assertEqual(categories.normalize_category("给猫买罐头"), "")
        self.assertEqual(categories.normalize_category(""), "")

    def test_normalized_is_always_known(self):
        for alias in list(categories.CATEGORY_ALIASES) + list(
                categories.CATEGORY_TO_ACCOUNTS):
            got = categories.normalize_category(alias)
            with self.subTest(alias=alias):
                self.assertIn(got, categories.CATEGORY_TO_ACCOUNTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
