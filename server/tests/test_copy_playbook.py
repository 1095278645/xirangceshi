# -*- coding: utf-8 -*-
"""文案打法库（copy_playbook / copy_review / 生成入口）单元与回归测试

这条能力是从一个成熟的生成型 skill 借鉴来的（把"随手写"拆成
渠道 × 骨架 × 语气 三个正交维度 + 配方 + 自动选型），所以测试重点也放在
**"组合是否完备、选型是否可解释、硬检查是否真的拦得住"**，而不是"文案好不好"。

另有一条硬回归：**降级文案必须一字不变**（`_copy_degraded` 有 14 处既有断言依赖它）。

运行：cd server && python -m pytest tests/test_copy_playbook.py -q
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import copy_playbook as pb  # noqa: E402
import copy_director as cd  # noqa: E402
import copy_review as cr  # noqa: E402
import config  # noqa: E402


class TestRegistryIntegrity(unittest.TestCase):
    """注册表自检：取值完备、交叉引用有效（对标 test_team 的域注册表自检）"""

    def test_dimensions_nonempty_and_unique(self):
        for name, values in (("CHANNELS", pb.CHANNELS), ("SKELETONS", pb.SKELETONS),
                             ("TONES", pb.TONES), ("RECIPES", pb.RECIPES)):
            self.assertTrue(values, f"{name} 不能为空")
            self.assertEqual(len(set(values)), len(values), f"{name} 不能有重复")

    def test_every_recipe_points_to_valid_dimensions(self):
        for name, spec in pb.RECIPE_SPECS.items():
            self.assertIn(name, pb.RECIPES, f"配方 {name} 没登记进 RECIPES")
            self.assertIn(spec["channel"], pb.CHANNELS, f"{name} 的渠道非法")
            self.assertIn(spec["skeleton"], pb.SKELETONS, f"{name} 的骨架非法")
            self.assertIn(spec["tone"], pb.TONES, f"{name} 的语气非法")
            for key in ("name", "best_for"):
                self.assertTrue(spec.get(key), f"{name} 缺少 {key}")

    def test_every_channel_and_skeleton_has_spec(self):
        for ch in pb.CHANNELS:
            spec = pb.channel_spec(ch)
            for key in ("name", "structure", "avoid", "soft_max", "hard_max", "scene_hint"):
                self.assertTrue(spec.get(key), f"渠道 {ch} 缺少 {key}")
            self.assertLess(spec["soft_max"], spec["hard_max"], f"{ch} 软上限应小于硬上限")
        for sk in pb.SKELETONS:
            spec = pb.skeleton_spec(sk)
            for key in ("name", "concept", "shape", "watch", "example"):
                self.assertTrue(spec.get(key), f"骨架 {sk} 缺少 {key}")
        for tn in pb.TONES:
            spec = pb.tone_spec(tn)
            for key in ("name", "feel", "rules", "banned_register"):
                self.assertTrue(spec.get(key), f"语气 {tn} 缺少 {key}")

    def test_invalid_values_raise(self):
        """铁律5：有限取值的非法输入必须报错，不许静默兜底。"""
        for call in (lambda: pb.channel_spec("weibo"), lambda: pb.skeleton_spec("nope"),
                     lambda: pb.tone_spec("angry"), lambda: pb.recipe_spec("nope")):
            with self.assertRaises(ValueError):
                call()
        for kwargs in ({"channel": "weibo"}, {"skeleton": "nope"}, {"tone": "x"},
                       {"recipe": "nope"}):
            with self.assertRaises(ValueError):
                pb.select_combo(**kwargs)

    def test_no_slop_words_unique(self):
        self.assertTrue(pb.NO_SLOP_WORDS)
        self.assertEqual(len(set(pb.NO_SLOP_WORDS)), len(pb.NO_SLOP_WORDS))


class TestAutoSelection(unittest.TestCase):
    """自动选型：意图信号 > 业态兜底 > 最常用兜底，且必须给得出人话理由"""

    def test_intent_signal_wins(self):
        combo = pb.select_combo(extra="小红书探店：新开的卤面馆")
        self.assertEqual(combo["channel"], "xiaohongshu")
        self.assertIn("小红书", combo["reason"])

    def test_group_signal(self):
        self.assertEqual(pb.select_combo(extra="群里通知一下，明天闭店")["channel"],
                         "wechat_group")

    def test_reply_signal(self):
        self.assertEqual(pb.select_combo(extra="有个差评要回复")["channel"], "reply")

    def test_industry_fallback(self):
        combo = pb.select_combo(scene="今日营业", extra="卤面", biz_type="餐饮")
        self.assertEqual(combo["recipe"], "moments_daily")
        self.assertIn("业态", combo["reason"])

    def test_no_clue_fallback(self):
        combo = pb.select_combo()
        self.assertEqual(combo["recipe"], "moments_daily")
        self.assertTrue(combo["reason"])

    def test_explicit_dimensions_are_respected(self):
        combo = pb.select_combo(channel="douyin", skeleton="listicle", tone="shout")
        self.assertEqual((combo["channel"], combo["skeleton"], combo["tone"]),
                         ("douyin", "listicle", "shout"))

    def test_recipe_expands_to_dimensions(self):
        combo = pb.select_combo(recipe="reply_sincere")
        self.assertEqual(combo["channel"], "reply")
        self.assertEqual(combo["tone"], "reply_sincere")

    def test_infer_biz_type(self):
        self.assertEqual(pb.infer_biz_type("今天包子出笼了"), "餐饮")
        self.assertEqual(pb.infer_biz_type("菜市场的青菜"), "生鲜")
        self.assertEqual(pb.infer_biz_type("随便一句话"), "")

    def test_brief_contains_discipline(self):
        combo = pb.select_combo(recipe="moments_daily")
        brief = pb.build_brief(combo, shop_name="老王面馆", extra="新出卤面")
        for token in ("朋友圈", "结构用", "语气用", "禁用词", "老王面馆", "新出卤面"):
            self.assertIn(token, brief)

    def test_combo_summary_gives_chinese_names(self):
        s = pb.combo_summary(pb.select_combo(recipe="xhs_note"))
        self.assertEqual(s["channel_name"], "小红书正文")
        self.assertTrue(s["skeleton_name"] and s["tone_name"])
        self.assertIn("limits", s)

    def test_explicit_channel_is_not_overridden_by_signal(self):
        """不变量①：显式选的渠道不许被意图信号覆盖（只该补空缺的维度）。"""
        combo = pb.select_combo(extra="小红书探店", channel="signboard")
        self.assertEqual(combo["channel"], "signboard")
        self.assertEqual(combo["skeleton"], "pain_open")       # 由信号补

    def test_recipe_always_matches_final_dimensions(self):
        """不变量②：recipe 必须与最终三维一致，否则报告会自相矛盾。

        自测抓到的原例：只传 channel=signboard，recipe 却留成 xhs_note →
        报告写"从「小红书」判断是小红书·探店笔记"，实际发的是招牌。
        """
        combo = pb.select_combo(extra="小红书探店", channel="signboard")
        spec = pb.RECIPE_SPECS.get(combo["recipe"])
        if spec:
            self.assertEqual((spec["channel"], spec["skeleton"], spec["tone"]),
                             (combo["channel"], combo["skeleton"], combo["tone"]))
        else:
            self.assertTrue(combo["recipe"].startswith("custom:"))
        self.assertNotIn("判断是", combo["reason"])
        self.assertIn("你指定了渠道", combo["reason"])

    def test_recipe_kept_when_nothing_explicit(self):
        """纯信号命中时仍报原名配方（便于复用同一个缩写）。"""
        combo = pb.select_combo(extra="小红书探店")
        self.assertEqual(combo["recipe"], "xhs_note")

    def test_recipe_combo_accepts_custom_form(self):
        """自定义组合（select_combo 产出）也要能展开，不能抛错。"""
        combo = pb.select_combo(extra="小红书探店", channel="signboard")
        self.assertTrue(combo["recipe"].startswith("custom:"))
        expanded = cd.recipe_combo(combo["recipe"])
        self.assertEqual(expanded["channel"], "signboard")
        self.assertEqual(expanded["recipe"], combo["recipe"])
        with self.assertRaises(ValueError):
            cd.recipe_combo("nope")

    def test_director_importable_without_playbook_first(self):
        """直接 import copy_director 不能因循环导入炸掉（曾经会）。"""
        import importlib
        import copy_director
        importlib.reload(copy_director)
        self.assertEqual(copy_director.select_combo(recipe="xhs_note")["channel"],
                         "xiaohongshu")

    def test_effective_limit_takes_the_stricter_one(self):
        """config 阈值与打法库声明取更严的那个，且诊断里显示真实生效值。"""
        import config
        old = config.COPY_MOMENTS_MAX_CHARS
        config.COPY_MOMENTS_MAX_CHARS = 10
        try:
            combo = pb.select_combo(recipe="moments_daily")
            self.assertEqual(cr.effective_limit("moments"), 10)
            r = cr.review("今天出卤面，8 块一碗。", combo)
            self.assertEqual(r["diagnostics"]["limit"], 10)
            self.assertIn("length", {c["id"] for c in r["checks"] if not c["ok"]})
        finally:
            config.COPY_MOMENTS_MAX_CHARS = old
        # 恢复后仍按打法库的 220
        self.assertEqual(cr.effective_limit("moments"), 220)


class TestImagePlan(unittest.TestCase):
    """配图方案：封面/内容/结尾，含图上文字与提示词；回评不配图"""

    def test_plan_has_cover_and_ratio(self):
        combo = pb.select_combo(recipe="xhs_note")
        plan = cr.build_image_plan(combo, shop_name="老王面馆", extra="招牌卤面",
                                   texts=["今天卤面 8 块一碗"])
        self.assertTrue(plan["required"])
        self.assertGreaterEqual(plan["count"], 1)
        self.assertEqual(plan["items"][0]["role"], "封面")
        for item in plan["items"]:
            self.assertTrue(item["prompt"] and item["scene"] and item["ratio"])
        self.assertEqual(plan["items"][0]["ratio"], "1:1")   # 小红书默认方图

    def test_moments_plan_is_single_image(self):
        plan = cr.build_image_plan(pb.select_combo(recipe="moments_daily"),
                                   shop_name="店", extra="卤面")
        self.assertEqual(plan["count"], 1)

    def test_reply_needs_no_image(self):
        plan = cr.build_image_plan(pb.select_combo(recipe="reply_sincere"),
                                   shop_name="店", extra="回复差评")
        self.assertFalse(plan["required"])
        self.assertEqual(plan["items"], [])

    def test_report_shape(self):
        combo = pb.select_combo(recipe="moments_daily")
        report = cr.build_report(combo, ["今天卤面出锅，8 块一碗，卖完收摊。"],
                                 team={"adopted": ["创意文案师"]}, shop_name="老王面馆")
        for key in ("combo", "texts", "primary", "reviews", "verdict", "image_plan"):
            self.assertIn(key, report)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["team_adopted"], ["创意文案师"])


class TestGenerateCopyIntegration(unittest.TestCase):
    """生成入口：降级文案一字不变 + 新参数可用 + 报告可返回"""

    def setUp(self):
        self._no_key = mock.patch("ai.ai_available", return_value=False)
        self._no_key.start()
        self.addCleanup(self._no_key.stop)

    def test_degraded_text_unchanged(self):
        """硬回归：降级文案必须与**既有实现原样**逐字一致（14 处既有测试依赖它）。

        断言用**字面量**而不是 `_copy_degraded(...)` 自比较 —— 后者恒真、测不出任何东西
        （独立复核指出的假绿：把 `_copy_degraded` 打桩改掉，自比较照样通过）。
        """
        import ai
        got = ai.generate_copy("老王面馆", "今日营业", "新出卤面", "")
        self.assertEqual(got, "【老王面馆】新出卤面\n—— 今日份营业，欢迎光临！"
                              "(提示：在设置页填入 API Key 后即可生成真实文案)")
        # 带 context 的另一条分支也要钉住（模板里会插一段括号说明）
        got2 = ai.generate_copy("老王面馆", "今日营业", "新出卤面", "", "今日收500元")
        self.assertEqual(got2, "【老王面馆】新出卤面（今日收500元）\n"
                               "—— 今日份营业，欢迎光临！"
                               "(提示：在设置页填入 API Key 后即可生成真实文案)")

    def test_degraded_return_process_still_works(self):
        import ai
        text, process, variants = ai.generate_copy(
            "老王面馆", "今日营业", "新出卤面", return_process=True)
        self.assertEqual(process["mode"], "collaborative")
        self.assertIn("合规审核", {e["role"] for e in process["employees"]})
        self.assertGreaterEqual(len(variants), 1)

    def test_report_mode_returns_pair_even_without_key(self):
        import ai
        text, report = ai.generate_copy(
            "老王面馆", "今日营业", "新出卤面", return_report=True, channel="signboard")
        self.assertIsInstance(text, str)
        self.assertEqual(report["combo"]["channel"], "signboard")
        self.assertIn("image_plan", report)

    def test_explicit_channel_changes_report_combo(self):
        import ai
        _, report = ai.generate_copy(
            "店", "今日营业", "群里通知明天闭店", return_report=True)
        self.assertEqual(report["combo"]["channel"], "wechat_group")

    def test_report_failure_does_not_break_text(self):
        """报告是附加物：它炸了也不能影响文案返回。"""
        import ai
        with mock.patch("copy_review.build_report", side_effect=RuntimeError("boom")):
            text, report = ai.generate_copy("店", "今日营业", "卤面", return_report=True)
        self.assertTrue(text)
        self.assertEqual(report["verdict"], "unknown")
        self.assertIn("error", report)


class TestSchemasMatchPlaybook(unittest.TestCase):
    """铁律5 回归：schemas 的 Literal 与 copy_playbook 的常量元组必须一致。"""

    def test_literals_match(self):
        import schemas
        from typing import get_args
        pairs = (("channel", pb.CHANNELS), ("skeleton", pb.SKELETONS),
                 ("tone", pb.TONES), ("recipe", pb.RECIPES))
        for field, constants in pairs:
            annotation = schemas.UnifiedInsightIn.model_fields[field].annotation
            literals = tuple(x for x in get_args(annotation) if isinstance(x, str))
            # 允许一个空串表示"不指定"（自动选型）
            self.assertEqual(sorted(x for x in literals if x), sorted(constants),
                             f"{field} 的 Literal 与 copy_playbook 常量已漂移")

    def test_config_limits_positive(self):
        for key in ("COPY_MOMENTS_MAX_CHARS", "COPY_XHS_BODY_MAX_CHARS",
                    "COPY_DOUYIN_MAX_CHARS", "COPY_GROUP_MAX_CHARS",
                    "COPY_SIGNBOARD_MAX_CHARS", "COPY_GROUPBUY_MAX_CHARS",
                    "COPY_REPLY_MAX_CHARS"):
            self.assertGreater(int(getattr(config, key)), 0, f"{key} 必须为正")
        self.assertGreater(float(config.COPY_MIN_CONCRETE_RATIO), 0.0)
