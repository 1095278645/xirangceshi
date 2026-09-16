"""一句话多笔：逐笔记账的回归测试（AI 打桩，不联网）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai
import config
import db


class MultiOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        config.DB_PATH = str(Path(cls._tmp.name) / "multi.db")
        db.DB_PATH = Path(config.DB_PATH)
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("transactions", "vouchers", "voucher_entries", "customers"):
                conn.execute(f"DELETE FROM {t}")

    def _client(self):
        import main
        from fastapi.testclient import TestClient
        return TestClient(main.app)

    def _stub(self, subs, **over):
        base = {"customer": "", "item": "", "amount": None, "note": "",
                "tags": "", "category": "", "trans_type": "income",
                "transactions": subs}
        base.update(over)
        return base

    def test_two_transactions_are_recorded_separately(self):
        """一句两笔要记成两条流水，**不能加在一起**。"""
        subs = [
            {"customer": "", "item": "卖货收入", "amount": 1250,
             "trans_type": "income", "category": "主营业务收入"},
            {"customer": "", "item": "买菜支出", "amount": 320,
             "trans_type": "expense", "category": "进货"},
        ]
        with mock.patch("ai.parse_transaction", return_value=self._stub(subs)):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "今天收入1250，支出320"}).json()
        self.assertTrue(j.get("multi"))
        self.assertEqual(len(j["recorded_list"]), 2)
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT item, amount, trans_type FROM transactions ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2, "必须是两条独立流水")
        self.assertEqual([r["amount"] for r in rows], [1250.0, 320.0])
        self.assertEqual([r["trans_type"] for r in rows], ["income", "expense"])
        total = sum(r["amount"] for r in rows)
        self.assertEqual(total, 1570.0, "两笔各自入账，不能被合并成一条")

    def test_total_is_not_summed_into_one(self):
        """反向守卫：绝不能把 50 和 80 合成 130 一条。"""
        subs = [{"customer": "", "item": "收款", "amount": 50,
                 "trans_type": "income", "category": "主营业务收入"},
                {"customer": "", "item": "收款", "amount": 80,
                 "trans_type": "income", "category": "主营业务收入"}]
        with mock.patch("ai.parse_transaction", return_value=self._stub(subs)):
            with self._client() as c:
                c.post("/api/orders", json={"text": "收了50，又收了80"})
        with db.get_conn() as conn:
            amts = [r[0] for r in conn.execute("SELECT amount FROM transactions")]
        self.assertEqual(sorted(amts), [50.0, 80.0])
        self.assertNotIn(130.0, amts, "不能出现 130 这种被加总的金额")

    def test_each_sub_gets_own_customer(self):
        """一句里不同的人各建各的熟客档案。"""
        subs = [{"customer": "刘姐", "item": "豆浆", "amount": 9,
                 "trans_type": "income", "category": "主营业务收入"},
                {"customer": "赵姐", "item": "包子", "amount": 15,
                 "trans_type": "income", "category": "主营业务收入"},
                {"customer": "小周", "item": "茶叶蛋", "amount": 6,
                 "trans_type": "income", "category": "主营业务收入"}]
        with mock.patch("ai.parse_transaction", return_value=self._stub(subs)):
            with self._client() as c:
                j = c.post("/api/orders",
                           json={"text": "刘姐买了9块，赵姐买了15块，小周买了6块"}).json()
        self.assertEqual(len(j["recorded_list"]), 3)
        with db.get_conn() as conn:
            names = sorted(r[0] for r in conn.execute("SELECT name FROM customers"))
            linked = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE customer_id IS NOT NULL"
            ).fetchone()[0]
        self.assertEqual(names, ["刘姐", "小周", "赵姐"])
        self.assertEqual(linked, 3, "三笔都要挂到各自的熟客上")

    def test_each_sub_generates_its_own_voucher(self):
        subs = [{"customer": "", "item": "卖货", "amount": 300,
                 "trans_type": "income", "category": "主营业务收入"},
                {"customer": "", "item": "买米", "amount": 100,
                 "trans_type": "expense", "category": "进货"}]
        with mock.patch("ai.parse_transaction", return_value=self._stub(subs)):
            with self._client() as c:
                c.post("/api/orders", json={"text": "卖了300块，另外买了100块的米"})
        with db.get_conn() as conn:
            n_v = conn.execute("SELECT COUNT(*) FROM vouchers").fetchone()[0]
            bal = conn.execute(
                "SELECT ROUND(SUM(CASE WHEN direction='debit' THEN amount ELSE 0 END),2),"
                " ROUND(SUM(CASE WHEN direction='credit' THEN amount ELSE 0 END),2) "
                "FROM voucher_entries").fetchone()
        self.assertEqual(n_v, 2, "每一笔都要有自己的凭证")
        self.assertEqual(bal[0], bal[1], "借贷仍要平衡")

    def test_backward_compatible_recorded_field(self):
        """老前端只读 recorded：多笔时至少给出第一笔，不能是空。"""
        subs = [{"customer": "", "item": "A", "amount": 10,
                 "trans_type": "income", "category": "主营业务收入"},
                {"customer": "", "item": "B", "amount": 20,
                 "trans_type": "income", "category": "主营业务收入"}]
        with mock.patch("ai.parse_transaction", return_value=self._stub(subs)):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "收了10，又收了20"}).json()
        self.assertEqual(j["recorded"]["amount"], 10)
        self.assertTrue(j["recorded"]["transaction_id"])

    def test_partial_missing_amount_does_not_write_partial(self):
        """多笔里有一笔没金额：整句都不落库，并列出缺的那几笔。

        "记一半"最坑：店主以为记完了，其实少一笔，月底对不上账。
        """
        subs = [{"customer": "", "item": "有金额", "amount": 30,
                 "trans_type": "income", "category": "主营业务收入"},
                {"customer": "张叔", "item": "没金额", "amount": None,
                 "trans_type": "income", "category": "主营业务收入"}]
        with mock.patch("ai.parse_transaction", return_value=self._stub(subs)):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "卖了30，张叔还拿了点别的"}).json()
        self.assertTrue(j["amount_missing"])
        self.assertTrue(j["multi"])
        self.assertIsNone(j["order_id"], "缺金额时不该产生订单")
        with db.get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.assertEqual(n, 0, "不能只记其中一笔")
        self.assertEqual(len(j["missing"]), 1)
        self.assertEqual(j["missing"][0]["customer"], "张叔")

    def test_single_transaction_still_normal(self):
        """只有一笔时不该被塞进列表（否则前端要多套一层）。"""
        with mock.patch("ai.parse_transaction",
                        return_value=self._stub([], amount=6, item="肉包",
                                                customer="王阿姨",
                                                category="主营业务收入")):
            with self._client() as c:
                j = c.post("/api/orders", json={"text": "王阿姨买了肉包6块"}).json()
        self.assertFalse(j.get("multi"))
        self.assertIsNone(j.get("recorded_list"))
        self.assertEqual(j["recorded"]["amount"], 6)


class NormalizeSubTransactionsTest(unittest.TestCase):
    """解析层的清洗：只留带金额的笔，分类归一。"""

    def test_unparseable_amount_is_kept_as_missing(self):
        """提了金额但解析不出来 → 保留为"缺金额"，而不是悄悄丢掉这一笔。

        丢掉等于"店主说了两笔、系统只记一笔"且不吭声。
        """
        got = ai._normalize_sub_transactions([
            {"amount": 10, "item": "a"}, {"amount": "abc", "item": "b"},
            {"item": "c"}, {"amount": 0},
        ])
        self.assertEqual(len(got), 4, "四条都保留")
        self.assertEqual([g["amount"] for g in got], [10.0, None, None, None])

    def test_requires_at_least_two(self):
        self.assertEqual(ai._normalize_sub_transactions([{"amount": 10}]), [])
        self.assertEqual(len(ai._normalize_sub_transactions(
            [{"amount": 10}, {"amount": 20}])), 2)

    def test_normalizes_category_alias(self):
        got = ai._normalize_sub_transactions([
            {"amount": 10, "category": "工资"}, {"amount": 20, "category": "工资"}])
        self.assertEqual([g["category"] for g in got], ["职工薪酬", "职工薪酬"])

    def test_bad_input_returns_empty(self):
        for bad in (None, "x", 5, {}, [1, 2]):
            self.assertEqual(ai._normalize_sub_transactions(bad), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
