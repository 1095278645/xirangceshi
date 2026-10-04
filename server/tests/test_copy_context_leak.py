# -*- coding: utf-8 -*-
"""回归用例：内部经营事项不得泄漏进对外文案。

背景（线上实测反馈）：用户看到朋友圈文案里写着
「今天想把冻品那笔1450追回来，拖两个多月了，刚打电话说死周五前」——
这是掌柜复盘里的催款待办，被当成"经营上下文"喂给模型后原样写进了对外文案。

两层防护，各测一条：
1. 预防层 `copy_rules.strip_internal()`：喂给模型之前就把内部句子剔掉；
2. 判定层 `copy_rules.HARD_RULES` 的 `context_leak`：万一还写出来了，评审判 fail。
外加一条 HTTP 级验证：确认进入模型提示词里的上下文已不含催款内容。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))

import copy_review  # noqa: E402
import copy_rules  # noqa: E402

LEAKED = "今天想把冻品那笔1450追回来，拖两个多月了，刚打电话说死周五前"
GOOD = "茶叶蛋卤得有点久，便宜出，五毛一个"


class StripInternalTest(unittest.TestCase):
    """预防层：内部句子进不了提示词。"""

    def test_drops_collection_sentence(self):
        got = copy_rules.strip_internal(f"{LEAKED}。{GOOD}")
        self.assertNotIn("1450", got)
        self.assertNotIn("冻品", got)
        self.assertIn("茶叶蛋", got)

    def test_keeps_plain_business_facts(self):
        got = copy_rules.strip_internal(GOOD)
        self.assertEqual(got, GOOD)

    def test_handles_empty_and_none(self):
        self.assertEqual(copy_rules.strip_internal(""), "")
        self.assertEqual(copy_rules.strip_internal(None), "")

    def test_splits_on_multiple_separators(self):
        got = copy_rules.strip_internal(f"{LEAKED}；新到土鸡蛋。{LEAKED}\n{GOOD}")
        self.assertNotIn("冻品", got)
        self.assertIn("新到土鸡蛋", got)
        self.assertIn("茶叶蛋", got)

    def test_review_words_also_dropped(self):
        for bad in ("本月毛利 62%", "账本流水 5510 元", "保本线 860 元",
                    "应收账款还有 3000", "这笔逾期两个月了"):
            with self.subTest(bad=bad):
                self.assertEqual(copy_rules.strip_internal(bad), "")


class ContextLeakRuleTest(unittest.TestCase):
    """判定层：真写出来了也要被判 fail。"""

    def test_rule_registered(self):
        ids = [r[0] for r in copy_rules.HARD_RULES]
        self.assertIn("context_leak", ids,
                      "copy_rules 必须登记 context_leak，否则模型泄漏时无人拦")

    def test_review_flags_leaked_copy(self):
        res = copy_review.review(f"今天把冻品那笔1450追回来，说死周五前。{GOOD}",
                                 combo=None, biz_type="早餐")
        vids = [v.get("id") for v in res.get("violations") or []]
        self.assertIn("context_leak", vids,
                      f"应判 context_leak，实际 violations={res.get('violations')}")
        self.assertEqual(res.get("verdict"), "fail")

    def test_clean_copy_not_flagged(self):
        res = copy_review.review(GOOD + "。青菜明天到期，今天买一送一，来晚就没了。",
                                 combo=None, biz_type="早餐")
        vids = [v.get("id") for v in res.get("violations") or []]
        self.assertNotIn("context_leak", vids)


class HttpContextTest(unittest.TestCase):
    """HTTP 级：确认喂给模型的上下文里已没有催款内容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        import config
        import shops
        self._orig = (config.DATA_DIR, config.DB_PATH, shops.SHOPS_DIR, shops.REGISTRY_PATH)
        config.DATA_DIR = str(root)
        config.DB_PATH = str(root / "ctx.db")
        shops.SHOPS_DIR = root / "shops"
        shops.REGISTRY_PATH = root / "registry.db"
        import db
        db.DB_PATH = Path(config.DB_PATH)
        db._schema_ready.clear()
        db.init_db()
        shops.init_registry()
        from fastapi.testclient import TestClient
        import main
        self.client = TestClient(main.app)
        self.db = db

    def tearDown(self):
        import config
        import shops
        (config.DATA_DIR, config.DB_PATH,
         shops.SHOPS_DIR, shops.REGISTRY_PATH) = self._orig
        self.db._schema_ready.clear()
        self._tmp.cleanup()

    def test_prompt_context_has_no_collection_content(self):
        self.db.set_domain_context("ledger", "daily_review", LEAKED)
        seen = {}

        def fake_chat(messages, **kw):
            seen["prompt"] = "\n".join(str(m.get("content") or "") for m in messages)
            return "茶叶蛋便宜出，五毛一个。|||青菜明天到期，买一送一。|||卖完收摊。"

        with mock.patch("ai.ai_available", return_value=True), \
             mock.patch("ai.chat", side_effect=fake_chat), \
             mock.patch("ai.load_settings", return_value={"api_key": "sk-x", "ai_pipeline": "team",
                                                         "model": "m", "base_url": "u"}):
            r = self.client.post("/api/insights", json={
                "scene": "copy",
                "payload": {"shop_name": "巷子口的早餐铺", "scene": "今日营业", "extra": ""}})
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("prompt", seen, "没有真的调用模型？")
        self.assertNotIn("1450", seen["prompt"],
                         "催款金额仍出现在模型提示词里 —— 预防层没生效")
        self.assertNotIn("追回来", seen["prompt"], "催款话术仍进入提示词")


if __name__ == "__main__":
    unittest.main(verbosity=2)
