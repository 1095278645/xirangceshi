# -*- coding: utf-8 -*-
"""文案接口契约测试：`POST /api/insights` 的打法维度与交付报告

单独成文件的原因与 `test_knowledge_http.py` 相同：单元测试全绿但"用户点得到的那条路"
不可用，是本项目反复踩到的坑（字段名对不上、参数没从顶层传进 payload）。

运行：cd server && python -m pytest tests/test_copy_http.py -q
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import copy_playbook as pb  # noqa: E402


class TestCopyHTTPContract(unittest.TestCase):
    """接口契约：`POST /api/insights` 的文案维度走 Literal（非法 422），报告可返回。

    单独在 HTTP 层再打一遍的理由：pytest 里的单元测试全绿但"用户点得到的那条路"不可用的
    情况，本项目踩过多次（字段名对不上、参数没往下传）。
    """

    def setUp(self):
        import tempfile
        import db
        self._tmp = tempfile.TemporaryDirectory()
        # 记下原路径，tearDown 必须还原 —— 否则后续测试模块会继续往这个已删除的临时库写，
        # 变成"跨用例的顺序依赖"（独立复核用探针复现过）。
        self._orig_db_path = db.DB_PATH
        self._orig_ready = set(db._schema_ready)
        db.DB_PATH = Path(self._tmp.name) / "copy_http.db"
        db._schema_ready.clear()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import basic
        app = FastAPI()
        app.include_router(basic.router)
        self.c = TestClient(app)
        self._no_key = mock.patch("ai.ai_available", return_value=False)
        self._no_key.start()
        self.addCleanup(self._no_key.stop)

    def tearDown(self):
        import db
        db.DB_PATH = self._orig_db_path
        db._schema_ready.clear()
        db._schema_ready.update(self._orig_ready)
        self._tmp.cleanup()

    def test_copy_with_channel_and_report(self):
        r = self.c.post("/api/insights", json={
            "scene": "copy",
            "payload": {"shop_name": "老王面馆", "scene": "今日营业", "extra": "新出卤面"},
            "channel": "xiaohongshu", "return_report": True})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("text"))
        self.assertEqual(body["combo"]["channel"], "xiaohongshu")
        self.assertIn(body["verdict"], ("pass", "warn", "fail"))
        self.assertIn("image_plan", body["report"])
        # 顶层维度要真的传进 payload（不是只做了校验）
        self.assertEqual(body["report"]["combo"]["channel"], "xiaohongshu")

    def test_invalid_channel_is_422(self):
        r = self.c.post("/api/insights", json={"scene": "copy", "channel": "weibo"})
        self.assertEqual(r.status_code, 422, "铁律5：非法渠道必须在请求期被拒")

    def test_invalid_recipe_is_422(self):
        r = self.c.post("/api/insights", json={"scene": "copy", "recipe": "nope"})
        self.assertEqual(r.status_code, 422)

    def test_default_copy_still_works_without_new_fields(self):
        """向后兼容：老客户端只传 payload 也必须能用。"""
        r = self.c.post("/api/insights", json={
            "scene": "copy", "payload": {"shop_name": "老王面馆", "extra": "新出卤面"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json().get("text"))
        self.assertIn(r.json()["combo"]["channel"], pb.CHANNELS)


if __name__ == "__main__":
    unittest.main()
