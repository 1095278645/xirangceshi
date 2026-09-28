# -*- coding: utf-8 -*-
"""OPC 执行闭环：熟客提醒"真的送达"（发送接口 + 留痕 + 自动补发幂等）。

运行：cd server && python -m unittest tests.test_reminder_send -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
import heartbeat
import notifications


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        db.DB_PATH = self.root / "rem.db"
        db._schema_ready.clear()
        db.init_db()
        self.addCleanup(db._schema_ready.clear)
        self._p = mock.patch.object(config, "DATA_DIR", self.root)
        self._p.start()
        self.addCleanup(self._p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import customers
        app = FastAPI()
        app.include_router(customers.router)
        return TestClient(app)

    def _mk_reminder(self):
        cid, _ = db.find_or_create_customer("王阿姨")
        return db.add_reminder(cid, "她胃不好，豆浆打热一点")


class TestReminderSend(_Base):
    def test_send_with_mock_channel_records_delivery(self):
        rid = self._mk_reminder()
        r = self._client().post(f"/api/reminders/{rid}/send", json={"channel": "mock"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        row = next(x for x in db.list_reminders() if x["id"] == rid)
        self.assertTrue(row["sent_at"])                    # 送达时间已留痕
        self.assertEqual(row["send_channel"], "mock")
        self.assertEqual(row["send_ok"], 1)
        self.assertTrue(notifications.recent_mock_messages())   # 本地收件箱有记录

    def test_auto_channel_defaults_to_mock_without_subscription(self):
        rid = self._mk_reminder()
        r = self._client().post(f"/api/reminders/{rid}/send")   # 无 body = 自动通道
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["channel"], "mock")

    def test_failed_delivery_is_recorded_and_returns_502(self):
        rid = self._mk_reminder()
        r = self._client().post(f"/api/reminders/{rid}/send",
                                json={"channel": "webhook",
                                      "target": "http://127.0.0.1:1/nope"})
        self.assertEqual(r.status_code, 502)
        row = next(x for x in db.list_reminders() if x["id"] == rid)
        self.assertEqual(row["send_ok"], 0)
        self.assertTrue(row["send_error"])                 # 失败原因入库 → 次日可重试

    def test_unknown_reminder_returns_404(self):
        self.assertEqual(self._client().post("/api/reminders/999999/send").status_code, 404)


class TestAutoResend(_Base):
    def test_push_pending_marks_sent_and_is_idempotent(self):
        rid = self._mk_reminder()
        first = heartbeat.push_pending_reminders()
        self.assertIn(rid, first["sent"])
        row = next(x for x in db.list_reminders() if x["id"] == rid)
        self.assertEqual(row["send_ok"], 1)
        # 再跑一次：已送达的不再重发（幂等）
        second = heartbeat.push_pending_reminders()
        self.assertEqual(second["sent"], [])

    def test_done_reminder_not_pushed(self):
        rid = self._mk_reminder()
        db.mark_reminder_done(rid, 1)
        self.assertEqual(heartbeat.push_pending_reminders()["sent"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
