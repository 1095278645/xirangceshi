# -*- coding: utf-8 -*-
"""文案硬检查与绕过手法的回归测试（规则表 / 归一化 / 已知局限）

从 tests/test_copy_playbook.py 拆出：那边放"打法库注册表 + 选型 + 集成"，
这里专门放**规则判定的对错**，包括独立复核抓到的几处绕过与误判。

运行：cd server && python -m pytest tests/test_copy_rules.py -q
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import copy_playbook as pb  # noqa: E402
import copy_review as cr  # noqa: E402
import config  # noqa: E402


class TestHardChecks(unittest.TestCase):
    """硬规则：绝对化用语/医疗/收益承诺/渠道禁忌/字数/具体度"""

    def setUp(self):
        self.combo = pb.select_combo(recipe="moments_daily")

    def _ids(self, result):
        return {c["id"] for c in result["checks"] if not c["ok"]}

    def test_absolute_terms_fail(self):
        r = cr.review("本店汤汁最好，全网最低价！", self.combo)
        self.assertIn("absolute_terms", self._ids(r))
        self.assertEqual(r["verdict"], "fail")

    def test_medical_claim_fails(self):
        r = cr.review("这碗汤能降血压、排毒养颜。", self.combo)
        self.assertIn("medical_claims", self._ids(r))
        self.assertEqual(r["verdict"], "fail")

    def test_channel_banned_words_fail(self):
        r = cr.review("今天有卤面，详情 http://a.com", self.combo)
        self.assertIn("channel_banned", self._ids(r))
        self.assertEqual(r["verdict"], "fail")

    def test_slop_words_warn(self):
        r = cr.review("我们致力于为您开启全新体验，匠心甄选。", self.combo)
        self.assertIn("ai_slop", self._ids(r))
        self.assertEqual(r["verdict"], "warn")

    def test_length_over_limit(self):
        long_text = "今天卤面出锅，" + "很好吃，" * 60
        r = cr.review(long_text, self.combo)
        self.assertIn("length", self._ids(r))

    def test_concreteness_floor(self):
        vague = "我们家东西很好吃。欢迎大家都来尝尝。品质一直很稳定。"
        r = cr.review(vague, self.combo)
        self.assertIn("concreteness", self._ids(r))

    def test_generic_tail_warn(self):
        r = cr.review("今天出卤面，8 块一碗。期待您的光临。", self.combo)
        self.assertIn("generic_tail", self._ids(r))

    def test_clean_text_passes(self):
        r = cr.review("今天卤面出锅，8 块一碗，卖完收摊。", self.combo)
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["violations"], [])
        self.assertEqual(r["next_actions"], [])

    def test_clean_text_has_no_hard_failures_in_every_channel(self):
        """每个渠道的干净文案都不许出现 **fail** —— 只测朋友圈会漏掉渠道特有规则的真 bug。

        独立复核抓到的 P1：招牌渠道的禁忌曾是「，」「；」两个**标点词**，归一化后变空串、
        `'' in flat` 恒真，导致招牌渠道任何非空文案都被判 fail，而全量测试全绿
        （因为没有一条"招牌干净文案"的用例）。

        这里断言的不是"必须 pass"：warn 里有些是**刻意的**提醒（比如回评文案没有数字），
        店主看得见、可以自己决定。硬红线（fail）才是必须零命中的。
        """
        samples = {
            "moments": "今天卤面出锅，8 块一碗，卖完收摊。",
            "xiaohongshu": "巷口那家卤面，8 块一碗，早上六点就开火。",
            "douyin": "6 斤肉包 120 个，卖完收摊。",
            "wechat_group": "明天闭店一天，后天早上 6 点照常开门。",
            "signboard": "卤面 8 块",
            "groupbuy": "套餐含卤面一碗、豆浆一杯，18 元，4 月 30 号前可用。",
            "reply": "是我们出餐慢了，那天人多没顾上，下次 6 点前下单我给你先下锅。",
        }
        for ch, text in samples.items():
            with self.subTest(channel=ch):
                r = cr.review(text, pb.select_combo(channel=ch))
                fails = [c["detail"] for c in r["checks"] if c["severity"] == "fail"]
                self.assertEqual(fails, [], f"{ch} 的干净文案被判 fail：{fails}")

    def test_punctuation_pileup_still_fails_for_short_channels(self):
        """反例：标点堆砌仍要拦（不能因为修上一处把这条判死了）。"""
        r = cr.review("卤面，，，8 块", pb.select_combo(channel="signboard"))
        self.assertIn("channel_banned", self._ids(r))
        self.assertEqual(r["verdict"], "fail")

    def test_fullwidth_and_case_variants_are_caught(self):
        """NFKC + casefold：全角数字/百分号、大小写、全角 @ 都要拦住。"""
        for text in ("１００％好吃", "＠全体成员 今天闭店", "YYDS 太好吃了", "详见 www.a.com"):
            with self.subTest(text=text):
                self.assertTrue(self._ids(cr.review(text, pb.select_combo())),
                                f"没拦住：{text}")

    def test_slop_words_caught_even_when_split(self):
        """空话套话也必须走归一化（原先走字面子串，拆字就能绕过）。"""
        r = cr.review("匠 心 甄 选，为您服务。", self.combo)
        self.assertIn("ai_slop", self._ids(r))

    def test_review_never_rewrites_text(self):
        """硬约束：只标记不改写（参考 skill 的"不许糊改"原则）。

        断言方式说明：字符串本身不可变，所以"没改输入"只能靠**传可变容器看它是否被就地改动**
        + **返回体里没有任何正文键**来证明（前者才是真正有意义的断言）。
        """
        dirty = "最好的卤面，为您甄选！"
        payload = [dirty]
        r = cr.review(dirty, self.combo)
        self.assertEqual(payload[0], dirty, "输入容器被就地改动了")
        self.assertNotIn("rewritten", r)
        self.assertNotIn("text", r)          # 返回里根本不带"改写后的正文"
        self.assertIn("只标记不改写", r["policy"])
        # 报告同样不许回写：传给 build_report 的列表不能被就地修改
        texts = [dirty, "今天 8 块一碗。"]
        snapshot = list(texts)
        cr.build_report(self.combo, texts)
        self.assertEqual(texts, snapshot)

    def test_verdict_takes_worst_of_list(self):
        combo = pb.select_combo(recipe="moments_daily")
        team = {"adopted": ["创意文案师"]}
        report = cr.build_report(combo, ["今天 8 块一碗，卖完收摊。", "全网最低最好吃！"],
                                 team=team)
        self.assertEqual(report["verdict"], "fail")

    def test_diagnostics_present(self):
        r = cr.review("今天 3 笼包子，6 块一个，5 点收摊。", self.combo)
        for key in ("chars", "lines", "emoji", "concrete_count", "concrete_ratio", "limit"):
            self.assertIn(key, r["diagnostics"])

    def test_bypass_attempts_are_caught_after_normalization(self):
        """拆字/分隔符/emoji/繁体都是常见绕过手法，归一化后必须照样拦下。"""
        for text in ("本店最 好 吃，全 网 最 低 价。",
                     "本店最·好·吃，绝·对·推·荐。",
                     "最好❤️吃，绝对推荐。",
                     "全網最低價，"):
            with self.subTest(text=text):
                r = cr.review(text, self.combo)
                self.assertIn("absolute_terms", self._ids(r), f"没拦住：{text}")

    def test_known_limit_synonyms_not_caught(self):
        """已知局限：同义替换（顶尖/首屈一指）不在词表里，字面匹配拦不住。

        这条**不是**要修的东西，而是把边界钉在测试里：改造词表或加语义检查时，
        如果突然能拦住了，说明能力提升了，届时把这个用例改成期望 fail 即可。
        """
        r = cr.review("本店顶尖水准，首屈一指。", self.combo)
        self.assertNotIn("absolute_terms", self._ids(r))

    def test_normalize_is_for_matching_only(self):
        """归一化只用于判定，不许改变输入文本（否则等于变相改写）。"""
        raw = "最 好 吃 ❤️"
        cr.review(raw, self.combo)
        self.assertEqual(raw, "最 好 吃 ❤️")
        self.assertEqual(cr.normalize_for_match("最 好 吃 ❤️"), "最好吃")


