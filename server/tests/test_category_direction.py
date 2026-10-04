# -*- coding: utf-8 -*-
"""分类「方向」一致性回归测试。

背景（线上实测）：模型偶发把支出挂到收入类科目 —— 出现过
`trans_type='expense'` + `category='其他收入'`。旧实现只做白名单归一、
**不校验方向**，于是 `_auto_voucher` 按分类映射科目，给一笔支出生成了
「借 库存现金 / 贷 其他业务收入」这样方向相反的凭证：账本品类与凭证一起错，
而且不报任何错，店主看不出来。

本文件锁三件事：
  1) `category_direction()` 能正确判出每个分类的方向（且新加分类自动跟着对）；
  2) `reconcile_category()` 在不一致时以**收支方向**为准并留痕；
  3) 走 HTTP 记账时，模型给错方向也不会落库成方向相反的账。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import categories
import db


class CategoryDirectionTest(unittest.TestCase):
    def test_income_categories(self):
        for c in ("主营业务收入", "其他收入"):
            self.assertEqual(categories.category_direction(c), "income", c)

    def test_expense_categories(self):
        for c in ("进货", "办公费", "租赁及物业费", "职工薪酬", "差旅费"):
            self.assertEqual(categories.category_direction(c), "expense", c)

    def test_unknown_returns_none(self):
        self.assertIsNone(categories.category_direction("给猫买罐头"))
        self.assertIsNone(categories.category_direction(""))

    def test_every_registered_category_has_a_direction(self):
        """新增分类如果没映射到收入/费用类科目，这里会红 —— 防止漏网。"""
        for c in categories.CATEGORY_TO_ACCOUNTS:
            with self.subTest(category=c):
                self.assertIn(categories.category_direction(c), ("income", "expense"))

    def test_direction_follows_the_account_not_a_hardcoded_list(self):
        """方向由科目类别推导：改映射表，方向自动跟着变。"""
        try:
            categories.CATEGORY_TO_ACCOUNTS["测试收入"] = ("1001", "5001", "测试")
            self.assertEqual(categories.category_direction("测试收入"), "income")
            categories.CATEGORY_TO_ACCOUNTS["测试收入"] = ("560101", "100201", "测试")
            self.assertEqual(categories.category_direction("测试收入"), "expense")
        finally:
            categories.CATEGORY_TO_ACCOUNTS.pop("测试收入", None)
            self.assertNotIn("测试收入", categories.CATEGORY_TO_ACCOUNTS)


class TransTypeNormalizeTest(unittest.TestCase):
    """DB 上 trans_type 有 CHECK(income/expense) 约束 —— 非法值必须在上游收敛。

    对抗式核对时实测：模型回「支出」/「EXPENSE」/空值时，INSERT 撞约束抛
    IntegrityError，记账接口**直接 500**：店主记不上账，报错信息还毫无意义。
    """

    def test_recognized_values(self):
        cases = {
            "income": "income", "Income": "income", "收入": "income", "进账": "income",
            "expense": "expense", "EXPENSE": "expense", "支出": "expense", "花费": "expense",
            " 支出 ": "expense",
        }
        for given, want in cases.items():
            with self.subTest(given=given):
                got, ok = categories.normalize_trans_type(given)
                self.assertTrue(ok, f"{given!r} 应被识别")
                self.assertEqual(got, want)

    def test_unknown_returns_empty_and_unrecognized(self):
        for given in ("", None, "transfer", "转账", "其他"):
            with self.subTest(given=given):
                got, ok = categories.normalize_trans_type(given)
                self.assertEqual(got, "")
                self.assertFalse(ok)


class ReconcileTest(unittest.TestCase):
    def test_consistent_pair_untouched(self):
        for cat, ttype in (("主营业务收入", "income"), ("办公费", "expense")):
            got, adjusted = categories.reconcile_category(cat, ttype)
            self.assertEqual(got, cat)
            self.assertFalse(adjusted)

    def test_expense_with_income_category_is_fixed(self):
        """线上实测的那一种：支出 + 其他收入 → 换成支出方向的通用科目。"""
        got, adjusted = categories.reconcile_category("其他收入", "expense")
        self.assertTrue(adjusted)
        self.assertEqual(got, "办公费")
        self.assertEqual(categories.category_direction(got), "expense")

    def test_income_with_expense_category_is_fixed(self):
        got, adjusted = categories.reconcile_category("进货", "income")
        self.assertTrue(adjusted)
        self.assertEqual(got, "主营业务收入")
        self.assertEqual(categories.category_direction(got), "income")

    def test_unknown_category_left_to_caller(self):
        """认不出来的分类不在这里兜底 —— 交给调用方的关键词兜底 + 留痕。"""
        got, adjusted = categories.reconcile_category("给猫买罐头", "expense")
        self.assertEqual(got, "给猫买罐头")
        self.assertFalse(adjusted)


class VoucherDirectionStaysSaneTest(unittest.TestCase):
    """端到端：方向错了也不能生成方向相反的凭证。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "dir.db"
        db._schema_ready.clear()
        db.init_db()

    def tearDown(self):
        self._tmp.cleanup()
        db._schema_ready.clear()

    def test_expense_never_credits_an_income_account(self):
        cat, adjusted = categories.reconcile_category("其他收入", "expense")
        self.assertTrue(adjusted)
        tid, voucher = db.add_transaction(None, "买面粉", 300, trans_type="expense",
                                          category=cat)
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT account_code, direction FROM voucher_entries WHERE voucher_id="
                "(SELECT id FROM vouchers WHERE transaction_id=?)", (tid,)).fetchall()
        codes = {r["account_code"]: r["direction"] for r in rows}
        self.assertTrue(codes, "应当生成了分录")
        for code, direction in codes.items():
            kind = categories.ACCOUNT_CATEGORY_OF.get(code)
            if direction == "credit":
                self.assertNotEqual(kind, "income",
                                    f"支出不该贷记收入类科目（{code}）")
            if direction == "debit":
                self.assertNotEqual(kind, "income",
                                    f"支出不该借记收入类科目（{code}）")


class RejectInconsistentEditTest(unittest.TestCase):
    """人工把分类改成方向不符时必须被拒（否则同样生成反方向凭证）。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "edit.db"
        db._schema_ready.clear()
        db.init_db()

    def tearDown(self):
        self._tmp.cleanup()
        db._schema_ready.clear()

    def test_edit_rejects_direction_mismatch(self):
        tid, _v = db.add_transaction(None, "买面粉", 300, trans_type="expense",
                                     category="办公费")
        with self.assertRaises(ValueError) as ctx:
            db.edit_transaction(tid, category="主营业务收入")
        self.assertIn("方向不一致", str(ctx.exception))

    def test_edit_allows_consistent_change(self):
        tid, _v = db.add_transaction(None, "买面粉", 300, trans_type="expense",
                                     category="办公费")
        out = db.edit_transaction(tid, category="进货")
        self.assertTrue(out.get("ok"))


class HttpPathTest(unittest.TestCase):
    """走 HTTP：模型返回方向矛盾的分类时，落库结果仍必须自洽。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        import config
        import shops
        self._orig = (config.DATA_DIR, config.DB_PATH,
                      shops.SHOPS_DIR, shops.REGISTRY_PATH)
        config.DATA_DIR = str(root)
        config.DB_PATH = str(root / "http.db")
        shops.SHOPS_DIR = root / "shops"
        shops.REGISTRY_PATH = root / "registry.db"
        db.DB_PATH = Path(config.DB_PATH)
        db._schema_ready.clear()
        db.init_db()
        shops.init_registry()
        from fastapi.testclient import TestClient
        import main
        self.client = TestClient(main.app)

    def tearDown(self):
        import config
        import shops
        (config.DATA_DIR, config.DB_PATH,
         shops.SHOPS_DIR, shops.REGISTRY_PATH) = self._orig
        db._schema_ready.clear()
        self._tmp.cleanup()

    def test_model_direction_mismatch_is_reconciled(self):
        # 模拟模型返回「支出 + 收入类分类」这种自相矛盾的结果
        fake = {
            "customer": "", "item": "买面粉", "amount": 300,
            "trans_type": "expense", "category": "其他收入",
            "note": "", "tags": "", "confidence": 0.9, "needs_check": False,
        }
        with mock.patch("ai.parse_transaction", return_value=fake), \
             mock.patch("ai.ai_available", return_value=True):
            r = self.client.post("/api/orders", json={"text": "买面粉花了300"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        rec = body.get("recorded") or {}
        self.assertEqual(rec.get("trans_type"), "expense")
        self.assertEqual(categories.category_direction(rec.get("category")), "expense",
                         f"落库分类方向必须与收支一致，实际 {rec.get('category')!r}")

    def test_multi_record_direction_mismatch_is_reconciled(self):
        """多笔路径同样要卡：第一笔故意给方向矛盾的分类。"""
        fake = {
            "customer": "", "item": "今天收入1250，支出320", "amount": None,
            "trans_type": "income", "category": "主营业务收入",
            "note": "", "tags": "", "confidence": 0.9, "needs_check": False,
            "transactions": [
                {"customer": "", "item": "今天收入1250", "amount": 1250,
                 "trans_type": "income", "category": "主营业务收入"},
                {"customer": "", "item": "支出320", "amount": 320,
                 "trans_type": "expense", "category": "其他收入"},   # ← 方向矛盾
            ],
        }
        with mock.patch("ai.parse_transaction", return_value=fake), \
             mock.patch("ai.ai_available", return_value=True):
            r = self.client.post("/api/orders", json={"text": "今天收入1250，支出320"})
        self.assertEqual(r.status_code, 200, r.text)
        rows = r.json().get("recorded_list") or []
        self.assertEqual(len(rows), 2, r.text)
        for row in rows:
            with self.subTest(row=row):
                self.assertEqual(categories.category_direction(row["category"]),
                                 row["trans_type"],
                                 f"分类 {row['category']!r} 与方向 {row['trans_type']} 不一致")


    # ---- 非法 trans_type：旧实现直接 500，现在必须能记上账 ----

    def _post_with_trans_type(self, ttype, category="办公费", text="买面粉花了300"):
        fake = {"customer": "", "item": "买面粉", "amount": 300,
                "trans_type": ttype, "category": category,
                "note": "", "tags": "", "confidence": 0.9, "needs_check": False}
        with mock.patch("ai.parse_transaction", return_value=fake), \
             mock.patch("ai.ai_available", return_value=True):
            return self.client.post("/api/orders", json={"text": text})

    def test_chinese_trans_type_does_not_500(self):
        for ttype, want in (("支出", "expense"), ("收入", "income"),
                            ("EXPENSE", "expense"), ("", "expense")):
            with self.subTest(ttype=ttype):
                r = self._post_with_trans_type(ttype)
                self.assertEqual(r.status_code, 200,
                                 f"trans_type={ttype!r} 不该 500（旧实现撞 DB 约束）：{r.text[:200]}")
                rec = r.json().get("recorded") or {}
                self.assertIn(rec.get("trans_type"), ("income", "expense"),
                              "落库方向必须是 income/expense")
                # 空值那一路没有方向线索，会计上按分类推成 expense（办公费）
                self.assertEqual(rec.get("trans_type"), want)

    def test_multi_with_chinese_trans_type_does_not_500(self):
        fake = {"customer": "", "item": "今天收入1250，支出320", "amount": None,
                "trans_type": "收入", "category": "主营业务收入",
                "note": "", "tags": "", "confidence": 0.9, "needs_check": False,
                "transactions": [
                    {"customer": "", "item": "收入1250", "amount": 1250,
                     "trans_type": "收入", "category": "主营业务收入"},
                    {"customer": "", "item": "支出320", "amount": 320,
                     "trans_type": "支出", "category": "办公费"},
                ]}
        with mock.patch("ai.parse_transaction", return_value=fake), \
             mock.patch("ai.ai_available", return_value=True):
            r = self.client.post("/api/orders", json={"text": "今天收入1250，支出320"})
        self.assertEqual(r.status_code, 200, r.text[:300])
        rows = r.json().get("recorded_list") or []
        self.assertEqual([x["trans_type"] for x in rows], ["income", "expense"])

    def test_all_written_rows_use_valid_direction(self):
        """凡是落库的方向，都必须是 income/expense —— 否则统计口径会漏计。"""
        self._post_with_trans_type("支出")
        with db.get_conn() as conn:
            bad = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE trans_type NOT IN ('income','expense')"
            ).fetchone()[0]
        self.assertEqual(bad, 0)


class CollectionDirectionTest(unittest.TestCase):
    """收款确认入账方向恒为收入：传支出类科目必须被拒，而不是生成反方向凭证。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        import config
        import shops
        self._orig = (config.DATA_DIR, config.DB_PATH,
                      shops.SHOPS_DIR, shops.REGISTRY_PATH)
        config.DATA_DIR = str(root)
        config.DB_PATH = str(root / "col.db")
        shops.SHOPS_DIR = root / "shops"
        shops.REGISTRY_PATH = root / "registry.db"
        db.DB_PATH = Path(config.DB_PATH)
        db._schema_ready.clear()
        db.init_db()
        shops.init_registry()
        from fastapi.testclient import TestClient
        import main
        self.client = TestClient(main.app)

    def tearDown(self):
        import config
        import shops
        (config.DATA_DIR, config.DB_PATH,
         shops.SHOPS_DIR, shops.REGISTRY_PATH) = self._orig
        db._schema_ready.clear()
        self._tmp.cleanup()

    def test_collection_rejects_expense_category(self):
        col = self.client.post("/api/collect/create",
                               json={"amount": 12.5, "item": "豆浆两杯"}).json()
        cid = col.get("id") or (col.get("collection") or {}).get("id")
        self.assertTrue(cid, col)
        r = self.client.post(f"/api/collect/{cid}/confirm", json={"category": "办公费"})
        self.assertGreaterEqual(r.status_code, 400,
                                f"收款入账用支出类科目应被拒，实际 {r.status_code}")

    def test_collection_accepts_income_category(self):
        col = self.client.post("/api/collect/create",
                               json={"amount": 12.5, "item": "豆浆两杯"}).json()
        cid = col.get("id") or (col.get("collection") or {}).get("id")
        r = self.client.post(f"/api/collect/{cid}/confirm",
                             json={"category": "主营业务收入"})
        self.assertEqual(r.status_code, 200, r.text[:200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
