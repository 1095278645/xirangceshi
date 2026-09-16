# -*- coding: utf-8 -*-
"""数据备份 / 导出 / 恢复测试。

这块直接关系到"账本会不会丢"，所以覆盖的不只是happy path，还包括：
  - 快照是否真的能恢复出**同样的数据**（不然备份是假的）
  - 非法文件是否被拒绝（避免把坏文件灌进库、把账本搞坏）
  - 恢复前是否自动留存当前数据（恢复错了能退回）
  - 自动备份清理是否**只删自动备份**，不误删手动备份与恢复前快照

运行：cd server && python -m unittest tests.test_backup -v
"""
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backup
import config
import db


class TestBackup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.db_file = cls.root / "data" / "ai_shopkeeper.db"
        cls.db_file.parent.mkdir(parents=True, exist_ok=True)
        # backup 模块实时读取 config.DB_PATH / config.DATA_DIR，
        # 所以必须 patch **config** 上的这两个值（而不是 backup 的模块属性）。
        cls._patches = [
            mock.patch.object(config, "DB_PATH", cls.db_file),
            mock.patch.object(config, "DATA_DIR", cls.root / "data"),
        ]
        for p in cls._patches:
            p.start()
        db.DB_PATH = cls.db_file
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            for t in ("transactions", "vouchers", "voucher_entries", "customers",
                      "memories", "reminders"):
                conn.execute(f"DELETE FROM {t}")
        # Windows 下删文件前必须确保没有连接占用；直接忽略占用错误，
        # 单个用例内部用的是各自新建的文件名，不会相互干扰
        import shutil as _sh
        _sh.rmtree(backup._backup_dir(), ignore_errors=True)

    def _seed(self, n=3):
        for i in range(n):
            db.add_transaction(None, f"测试{i}", 100 + i, "income", "主营业务收入")

    def _count(self):
        with db.get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    # ---------------- 创建 / 列表 ----------------

    def test_create_backup_and_list(self):
        self._seed(3)
        info = backup.create_backup("manual", note="单测")
        self.assertTrue(Path(info["path"]).is_file())
        self.assertEqual(info["stats"]["transactions"], 3)
        items = backup.list_backups()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "manual")
        self.assertEqual(items[0]["name"], info["name"])

    def test_snapshot_is_readable_sqlite_with_same_data(self):
        """快照必须是可独立打开的合法库，且数据一致 —— 否则备份等于没有。"""
        self._seed(5)
        info = backup.create_backup("manual")
        # 注意：`with sqlite3.connect(...)` 只提交事务、**不关闭连接**，
        # 在 Windows 上会一直占着文件导致后续清理失败。必须显式 close。
        conn = sqlite3.connect(info["path"])
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 5)

    # ---------------- 导出 ----------------

    def test_export_bundle_contents_without_config(self):
        self._seed(2)
        info = backup.export_bundle(include_config=False)
        with zipfile.ZipFile(info["path"]) as z:
            names = z.namelist()
            self.assertIn(backup.DB_IN_BUNDLE, names)
            self.assertIn(backup.MANIFEST_NAME, names)
            self.assertNotIn(backup.CONFIG_NAME, names,
                             "默认不能把 API Key 打进导出包")
            manifest = json.loads(z.read(backup.MANIFEST_NAME).decode("utf-8"))
            self.assertEqual(manifest["stats"]["transactions"], 2)
            self.assertFalse(manifest["includes_config"])

    def test_export_bundle_can_include_config(self):
        info = backup.export_bundle(include_config=True)
        with zipfile.ZipFile(info["path"]) as z:
            self.assertTrue(z.read(backup.MANIFEST_NAME))
        # 配置是否存在取决于本机是否配了 Key，这里只验证不报错
        self.assertTrue(Path(info["path"]).is_file())

    # ---------------- 恢复 ----------------

    def test_restore_recovers_exact_data(self):
        """核心：备份 3 笔 → 再写 2 笔 → 恢复 → 必须回到 3 笔。"""
        self._seed(3)
        snap = backup.create_backup("manual")
        self._seed(2)
        self.assertEqual(self._count(), 5)

        backup.restore_from_file(Path(snap["path"]))
        self.assertEqual(self._count(), 3, "恢复后应回到备份时的数据")

    def test_restore_takes_pre_restore_snapshot(self):
        """恢复前必须自动留存当前数据，否则恢复错了没法退回。"""
        self._seed(1)
        snap = backup.create_backup("manual")
        self._seed(4)                      # 当前 5 笔
        result = backup.restore_from_file(Path(snap["path"]))
        self.assertTrue(result["pre_restore_snapshot"], "应返回恢复前快照名")
        # 那份快照里应有恢复前的 5 笔
        pre = backup._backup_dir() / result["pre_restore_snapshot"]
        self.assertTrue(pre.is_file())
        conn = sqlite3.connect(str(pre))
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 5)

    def test_restore_from_bundle_zip(self):
        self._seed(2)
        bundle = backup.export_bundle()
        self._seed(6)
        self.assertEqual(self._count(), 8)
        backup.restore_from_bundle(Path(bundle["path"]))
        self.assertEqual(self._count(), 2)

    def test_restore_accepts_plain_db_file(self):
        self._seed(2)
        snap = backup.create_backup("manual")
        self._seed(3)
        backup.restore_from_bundle(Path(snap["path"]))   # .db 也接受
        self.assertEqual(self._count(), 2)

    # ---------------- 校验（防止坏文件灌进库） ----------------

    def test_rejects_non_sqlite_file(self):
        junk = self.root / "junk.db"
        junk.write_text("这不是数据库", encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            backup.restore_from_file(junk)
        self.assertIn("SQLite", str(cm.exception))

    def test_rejects_empty_file(self):
        empty = self.root / "empty.db"
        empty.write_bytes(b"")
        with self.assertRaises(ValueError):
            backup.restore_from_file(empty)

    def test_rejects_sqlite_without_required_tables(self):
        """别的 SQLite 库（比如随便一个 sqlite 文件）不能拿来覆盖账本。"""
        other = self.root / "other.db"
        conn = sqlite3.connect(str(other))
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE unrelated(x INTEGER)")
        conn.commit()
        with self.assertRaises(ValueError) as cm:
            backup.restore_from_file(other)
        self.assertIn("必需的表", str(cm.exception))

    def test_rejects_zip_without_db(self):
        bad = self.root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("readme.txt", "no db here")
        with self.assertRaises(ValueError) as cm:
            backup.restore_from_bundle(bad)
        self.assertIn(backup.DB_IN_BUNDLE, str(cm.exception))

    def test_bad_restore_does_not_damage_current_db(self):
        """失败的恢复绝不能破坏当前数据。"""
        self._seed(3)
        junk = self.root / "junk2.db"
        junk.write_text("垃圾", encoding="utf-8")
        with self.assertRaises(ValueError):
            backup.restore_from_file(junk)
        self.assertEqual(self._count(), 3, "校验失败时当前数据应原样不动")

    # ---------------- 自动备份与清理 ----------------

    def test_prune_only_removes_auto_backups(self):
        """清理只能删自动备份，不能误删手动备份与恢复前快照。"""
        with mock.patch.object(backup, "KEEP_AUTO_BACKUPS", 2):
            for _ in range(4):
                backup.create_backup("auto")
            backup.create_backup("manual")
            backup.create_backup("pre_restore")
        autos = [p for p in backup._backup_dir().glob("auto-*.db")]
        self.assertEqual(len(autos), 2, "自动备份应只保留 2 份")
        self.assertEqual(len(list(backup._backup_dir().glob("manual-*.db"))), 1)
        self.assertEqual(len(list(backup._backup_dir().glob("pre_restore-*.db"))), 1)

    def test_auto_backup_throttled(self):
        """刚备份过就不该重复备份（避免频繁重启时刷出一堆）。"""
        self.assertIsNotNone(backup.auto_backup_if_needed())
        self.assertIsNone(backup.auto_backup_if_needed(), "20 小时内不应再备份")

    def test_backup_survives_wal_mode(self):
        """WAL 模式下备份也要拿到最新数据（VACUUM INTO 的一致性保证）。"""
        self._seed(1)
        with db.get_conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        self._seed(2)                      # 这 2 笔可能还在 WAL 里
        info = backup.create_backup("manual")
        conn = sqlite3.connect(info["path"])
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 3,
            "备份必须包含最新提交，不能丢 WAL 中的数据")


if __name__ == "__main__":
    unittest.main(verbosity=2)
