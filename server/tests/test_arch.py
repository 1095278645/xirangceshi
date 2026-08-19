# -*- coding: utf-8 -*-
"""架构落地测试：领域上下文 / 任务队列 / 单店档案 / 心跳复盘

运行：cd server && python -m unittest tests.test_arch -v
"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import heartbeat


class _TempDB(unittest.TestCase):
    """每个测试类共用一个临时库，建表 + 清空三张新表"""
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(cls._tmp.name) / "test_arch.db"
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM domain_context")
            conn.execute("DELETE FROM job_tasks")
            conn.execute("DELETE FROM store_profiles")


class TestDomainContext(_TempDB):
    def test_set_and_get(self):
        db.set_domain_context("ledger", "today", {"income": 100})
        item = db.get_domain_context("ledger", "today")
        self.assertEqual(item["value"], {"income": 100})

    def test_upsert_updates_value(self):
        db.set_domain_context("ledger", "k", "v1")
        db.set_domain_context("ledger", "k", "v2")
        item = db.get_domain_context("ledger", "k")
        self.assertEqual(item["value"], "v2")

    def test_get_missing_returns_none(self):
        self.assertIsNone(db.get_domain_context("nope", "x"))

    def test_list_by_domain(self):
        db.set_domain_context("ledger", "a", "1")
        db.set_domain_context("ledger", "b", "2")
        db.set_domain_context("customer", "a", "3")
        items = db.list_domain_context("ledger")
        self.assertEqual({i["key"] for i in items}, {"a", "b"})

    def test_list_all(self):
        db.set_domain_context("ledger", "a", "1")
        db.set_domain_context("customer", "b", "2")
        self.assertEqual(len(db.list_domain_context(None)), 2)


class TestJobQueue(_TempDB):
    def test_enqueue_and_claim(self):
        jid = db.enqueue_job("heartbeat", {"day": "today"})
        job = db.claim_next_job()
        self.assertEqual(job["id"], jid)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["payload"], {"day": "today"})

    def test_claim_none_when_empty(self):
        self.assertIsNone(db.claim_next_job())

    def test_mark_done(self):
        jid = db.enqueue_job("report")
        db.claim_next_job()
        db.mark_job_done(jid, {"ok": True})
        items = db.list_jobs(status="done")
        self.assertEqual(items[0]["result"], '{"ok": true}')

    def test_retry_then_dead(self):
        jid = db.enqueue_job("push", max_retries=2)
        # 第 1 次失败 → 回 pending（requeued）
        self.assertEqual(db.mark_job_failed(jid, "err1"), "requeued")
        self.assertEqual(db.list_jobs(status="pending")[0]["retries"], 1)
        # 第 2 次失败 → 达上限 → dead
        self.assertEqual(db.mark_job_failed(jid, "err2"), "dead")
        self.assertEqual(db.list_jobs(status="dead")[0]["retries"], 2)

    def test_requeue_dead(self):
        jid = db.enqueue_job("push", max_retries=1)
        db.mark_job_failed(jid, "boom")
        db.mark_job_failed(jid, "boom")
        db.requeue_job(jid)
        items = db.list_jobs(status="pending")
        self.assertEqual(items[0]["id"], jid)
        self.assertEqual(items[0]["retries"], 0)


class TestStoreProfile(_TempDB):
    def test_save_and_load(self):
        pid = db.save_store_profile(
            "老王面馆", biz_type="餐饮", rent=6000, salary=8000,
            utilities=2000, total_investment=100000, cash_on_hand=30000)
        p = db.load_store_profile(pid)
        self.assertEqual(p["name"], "老王面馆")
        self.assertEqual(p["rent"], 6000)

    def test_update_profile(self):
        pid = db.save_store_profile("店A", biz_type="餐饮")
        db.save_store_profile("店A改名", profile_id=pid, biz_type="饮品")
        p = db.load_store_profile(pid)
        self.assertEqual(p["name"], "店A改名")
        self.assertEqual(p["biz_type"], "饮品")

    def test_list_and_delete(self):
        pid = db.save_store_profile("店B")
        self.assertEqual(len(db.list_store_profiles()), 1)
        self.assertTrue(db.delete_store_profile(pid))
        self.assertEqual(len(db.list_store_profiles()), 0)


class TestHeartbeat(_TempDB):
    def test_generate_and_read(self):
        # 插入一笔今日流水，让今日汇总有数
        today = date.today().isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO transactions(trans_type, category, item, amount, created_at) "
                "VALUES('income','主营业务收入','卖货',100,?)", (f"{today} 10:00:00",))
        text = heartbeat.generate_daily_review()
        self.assertIn("今日", text)
        self.assertEqual(heartbeat.daily_review_text(), text)

    def test_no_profile_still_generates(self):
        # 无店档案时也要能生成复盘（含引导建档案一句话）
        text = heartbeat.generate_daily_review()
        self.assertTrue(text)
        self.assertIn("今日", text)

    def test_one_liner_with_profile(self):
        db.save_store_profile("测试店", biz_type="餐饮", rent=6000, salary=3000,
                              utilities=1000, total_investment=50000,
                              cash_on_hand=20000)
        profile = db.list_store_profiles()[0]
        line = heartbeat._one_liner(profile)
        self.assertTrue(line.startswith("[单店"))


if __name__ == "__main__":
    unittest.main(verbosity=2)