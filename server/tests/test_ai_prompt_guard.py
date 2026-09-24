# -*- coding: utf-8 -*-
"""L11 速度硬约束：提示词预算护栏（告警 + 截断 + 保留 system）。

背景：项目里散落着各处的 `[:N]` 自觉截断，容易随迭代退化。`ai.chat` 入口统一兜底，
保证任何"脚本原始大输出"都不会未经处理直接喂给模型。

运行：cd server && python -m unittest tests.test_ai_prompt_guard -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai
import config
import db


class TestPromptBudgetHelper(unittest.TestCase):
    def test_short_prompt_untouched(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
        with mock.patch.object(config, "AI_PROMPT_WARN_CHARS", 100), \
                mock.patch.object(config, "AI_PROMPT_MAX_CHARS", 200):
            self.assertIs(ai._enforce_prompt_budget(msgs), msgs)

    def test_warn_only_keeps_content(self):
        msgs = [{"role": "user", "content": "x" * 150}]
        with mock.patch.object(config, "AI_PROMPT_WARN_CHARS", 100), \
                mock.patch.object(config, "AI_PROMPT_MAX_CHARS", 200):
            out = ai._enforce_prompt_budget(msgs, domain="test")
        self.assertEqual(ai._prompt_chars(out), 150)

    def test_truncates_beyond_max_and_preserves_system(self):
        msgs = [{"role": "system", "content": "S" * 80},
                {"role": "user", "content": "U" * 500}]
        with mock.patch.object(config, "AI_PROMPT_WARN_CHARS", 100), \
                mock.patch.object(config, "AI_PROMPT_MAX_CHARS", 200):
            out = ai._enforce_prompt_budget(msgs)
        self.assertLessEqual(ai._prompt_chars(out), 200)
        # system 全文保留
        sys_msg = next(m for m in out if m["role"] == "system")
        self.assertEqual(sys_msg["content"], "S" * 80)

    def test_malformed_messages_do_not_crash(self):
        self.assertEqual(ai._prompt_chars([None, {}, {"content": None}]), 0)


class _FakeClient:
    def __init__(self, box):
        self._box = box
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kw):
        self._box.append(kw)
        msg = type("M", (), {"content": "ok", "reasoning_content": ""})()
        choice = type("Ch", (), {"message": msg})()
        return type("R", (), {"choices": [choice], "usage": None})()


class TestChatWiring(unittest.TestCase):
    """确认护栏真的接在 ai.chat 上（不是只写了函数）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmp.name) / "guard.db"
        db._schema_ready.clear()
        db.init_db()
        self.addCleanup(db._schema_ready.clear)

    def tearDown(self):
        self._tmp.cleanup()

    def test_chat_truncates_before_calling_model(self):
        box = []
        with mock.patch.object(ai, "get_client", return_value=_FakeClient(box)), \
                mock.patch.object(ai, "load_settings", return_value={"model": "m"}), \
                mock.patch.object(config, "AI_PROMPT_WARN_CHARS", 100), \
                mock.patch.object(config, "AI_PROMPT_MAX_CHARS", 200):
            ai.chat([{"role": "system", "content": "S" * 80},
                     {"role": "user", "content": "U" * 500}])
        sent = box[0]["messages"]
        self.assertLessEqual(ai._prompt_chars(sent), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
