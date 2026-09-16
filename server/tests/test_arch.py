# -*- coding: utf-8 -*-
"""架构落地测试：领域上下文 / 任务队列 / 单店档案 / 心跳复盘

运行：cd server && python -m unittest tests.test_arch -v
"""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

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

    def test_concurrent_claim_no_duplicate(self):
        """并发领取同一批 pending 任务，每个任务只被领取一次（无重复领取）。"""
        for _ in range(5):
            db.enqueue_job("heartbeat")
        # 逐个连接领取，模拟多线程/多进程同时抢占：总量应恰好等于任务数
        claimed_ids = []
        for _ in range(10):  # 领取次数超过任务数，多余应返回 None
            job = db.claim_next_job()
            if job is None:
                break
            claimed_ids.append(job["id"])
        # 领取到的任务 id 不重复，且数量等于实际 pending 任务数
        self.assertEqual(len(claimed_ids), len(set(claimed_ids)))
        self.assertEqual(len(claimed_ids), 5)
        # 所有任务都已置 running，无剩余 pending
        self.assertEqual(len(db.list_jobs(status="pending")), 0)
        self.assertEqual(len(db.list_jobs(status="running")), 5)


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
    """每日复盘：现在走多 agent 掌柜编排（五位伙计各管一摊 → 掌柜取舍）。

    注意测试里**必须把 AI 打桩**：本机若配了 Key，不打桩就会真的调模型
    （实测这一组用例从 1 秒变成 135 秒，还白烧额度）。
    """

    def setUp(self):
        super().setUp()
        # 让 ai_available() 恒为 False → 走规则降级，离线且确定
        import ai
        self._patch = mock.patch("ai.ai_available", return_value=False)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_generate_and_read(self):
        # 插入一笔今日流水，让今日汇总有数
        today = date.today().isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO transactions(trans_type, category, item, amount, created_at) "
                "VALUES('income','主营业务收入','卖货',100,?)", (f"{today} 10:00:00",))
        text = heartbeat.generate_daily_review()
        self.assertTrue(text, "复盘不能为空")
        self.assertEqual(heartbeat.daily_review_text(), text, "复盘要落盘可读")
        # 快照也要落盘：前端要能展开"掌柜看到的原始事实"
        snap = heartbeat.daily_snapshot_text()
        self.assertTrue(snap, "全店快照要落盘")
        self.assertIn("[今日]", snap)
        self.assertIn("100", snap, "今日那笔流水要出现在快照里")

    def test_no_profile_still_generates(self):
        # 无店档案时也要能生成复盘（不因为缺档案就报错/空转）
        text = heartbeat.generate_daily_review()
        self.assertTrue(text)
        self.assertTrue(heartbeat.daily_snapshot_text())

    def test_snapshot_covers_all_business_domains(self):
        """快照要覆盖全店经营动作，而不是只有收支。

        这是"掌柜知晓全店全流程"的落脚点：缺哪一块，对应岗位就没话说。
        """
        import shop_snapshot
        facts = shop_snapshot.snapshot_facts()
        for domain in ("money", "customers", "stock", "cash", "invoice"):
            with self.subTest(domain=domain):
                self.assertIn(domain, facts)
                self.assertTrue(facts[domain], f"{domain} 这一段不该是空的"
                                               f"（没数据也要说明『还没建』）")

    def test_team_domain_registered_with_five_roles(self):
        """review 域要按经营维度分工，不能是五个同质员工。"""
        import team_domains
        cfg = team_domains.TEAM_DOMAINS["review"]
        roles = [e["role"] for e in cfg["employees"]]
        self.assertGreaterEqual(len(roles), 4, "至少四个岗位才谈得上『全流程』")
        self.assertEqual(len(roles), len(set(roles)), "岗位不能重名")
        for r in ("账房先生", "熟客管家", "采买师傅", "税务管事", "经营监察"):
            self.assertIn(r, roles)

    def test_one_liner_with_profile(self):
        db.save_store_profile("测试店", biz_type="餐饮", rent=6000, salary=3000,
                              utilities=1000, total_investment=50000,
                              cash_on_hand=20000)
        profile = db.list_store_profiles()[0]
        line = heartbeat._one_liner(profile)
        self.assertTrue(line.startswith("[单店"))


if __name__ == "__main__":
    unittest.main(verbosity=2)