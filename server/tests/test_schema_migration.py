# -*- coding: utf-8 -*-
"""数据层 schema 自动迁移测试。

背景：迁移原先只在 FastAPI 的 lifespan 里调 `init_db()`。任何**在迁移之前**
访问数据层的路径（直接调函数、脚本、后台线程）都会撞上
`no such column: status` —— 实测老库升级时踩到过。

现在 get_conn() 会兜底自检并跑迁移，本测试验证：
  - 老库（缺 status 列）被自动升级，不报错
  - 自检不会无限递归（init_db 内部也走 get_conn）
  - 新库正常

运行：cd server && python -m unittest tests.test_schema_migration -v
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db


class TestSchemaMigration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_file = Path(self._tmp.name) / "old.db"
        db.DB_PATH = self.db_file
        # 每个用例都用新路径：_schema_ready 是按路径缓存的，必须清掉缓存
        db._schema_ready.clear()
        self.addCleanup(db._schema_ready.clear)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_legacy_db(self):
        """造一个"老版本"的库：有 transactions，但没有 status 等新列。"""
        conn = sqlite3.connect(str(self.db_file))
        try:
            conn.execute("""
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER,
                    trans_type TEXT NOT NULL DEFAULT 'income',
                    category TEXT DEFAULT '主营业务收入',
                    item TEXT DEFAULT '',
                    amount REAL DEFAULT 0,
                    counterparty TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )""")
            conn.execute("INSERT INTO transactions(item, amount, trans_type, category) "
                         "VALUES('老数据', 88, 'income', '主营业务收入')")
            conn.commit()
        finally:
            conn.close()

    def test_legacy_db_auto_migrates_on_first_access(self):
        """核心：直接访问数据层即自动升级，不需要先手动 init_db。"""
        self._make_legacy_db()
        # 不做任何 init_db 调用，直接查 —— 这在修复前会抛 no such column: status
        rows = db.list_transactions()
        self.assertEqual(len(rows), 1, "老数据应可读")

        summary = db.monthly_summary()
        self.assertGreaterEqual(summary["income"], 0)

        stats = db.store_ledger_stats()
        self.assertIsNotNone(stats)

    def test_migration_adds_expected_columns(self):
        self._make_legacy_db()
        db.list_transactions()        # 触发迁移
        conn = sqlite3.connect(str(self.db_file))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
        finally:
            conn.close()
        for col in ("status", "voided_reason", "parent_id", "source", "wx_trade_id"):
            self.assertIn(col, cols, f"迁移后应存在列 {col}")

    def test_migration_creates_audit_table(self):
        self._make_legacy_db()
        db.list_transactions()
        conn = sqlite3.connect(str(self.db_file))
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        self.assertIn("transaction_audits", names)

    def test_no_infinite_recursion(self):
        """init_db 内部也走 get_conn，自检必须先登记再迁移，否则无限递归。"""
        self._make_legacy_db()
        try:
            db.list_transactions()
        except RecursionError as e:  # noqa: BLE001
            self.fail(f"schema 自检发生递归：{e}")

    def test_legacy_rows_default_to_active(self):
        """迁移后老数据的 status 应视为 active，不能被合计排除。"""
        self._make_legacy_db()
        db.list_transactions()
        rows = db.list_transactions()
        self.assertEqual(len(rows), 1, "status 为 NULL 的老数据必须仍计入")

    def test_schema_version_recorded(self):
        """迁移完成后写入版本号。

        早期只检查 transactions.status 这一列，导致**新增的表不会被创建** ——
        实测老库访问 opening_balances 时崩在 "no such table"。
        """
        db.init_db()
        with db.get_conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, db.SCHEMA_VERSION)

    def test_new_tables_created_on_legacy_db(self):
        """老库自动补齐后来新增的表。"""
        self._make_legacy_db()
        db.list_transactions()          # 触发迁移
        conn = sqlite3.connect(str(self.db_file))
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        for table in ("opening_balances", "period_closings", "notification_logs",
                      "notification_subscriptions", "payment_collections",
                      "transaction_audits"):
            self.assertIn(table, names, f"迁移后应创建表 {table}")

    def test_fresh_db_works(self):
        db.init_db()
        db.add_transaction(None, "新库", 10, "income", "主营业务收入")
        self.assertEqual(len(db.list_transactions()), 1)

    def test_schema_check_runs_once_per_path(self):
        """自检按路径缓存，不该每次连接都查 PRAGMA。"""
        self._make_legacy_db()
        db.list_transactions()
        self.assertIn(str(db.DB_PATH), db._schema_ready)
        calls = []
        original = db.init_db

        def counting():
            calls.append(1)
            return original()

        db.init_db = counting
        try:
            for _ in range(5):
                db.list_transactions()
        finally:
            db.init_db = original
        self.assertEqual(calls, [], "已就绪的库不该重复迁移")


if __name__ == "__main__":
    unittest.main(verbosity=2)
