# -*- coding: utf-8 -*-
"""评测报告纯逻辑测试（离线，不调模型）。

评测脚本本身要真调模型（慢、耗额度），但"报告怎么算"必须能被单测覆盖，
否则只有跑真模型才知道报表算错。

运行：cd server && python -m unittest tests.test_eval_report -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eval_report


class TestDimensionSummary(unittest.TestCase):
    def test_overall_and_category(self):
        hits = {"amount": 9, "direction": 8, "category": 7, "customer": 6, "ok": 5}
        per_cat = {"金额": {"n": 10, "ok": 5}, "多笔": {"n": 0, "ok": 0}}
        s = eval_report.dimension_summary(hits, 10, per_cat)
        self.assertEqual(s["total"], 10)
        self.assertEqual(s["overall"]["金额"]["pct"], 90.0)
        self.assertEqual(s["overall"]["四项全对"]["hit"], 5)
        self.assertEqual(s["per_category"]["金额"]["pct"], 50.0)
        self.assertEqual(s["per_category"]["多笔"]["pct"], 0.0)

    def test_zero_total_no_division_error(self):
        s = eval_report.dimension_summary({}, 0, {})
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["overall"]["金额"]["pct"], 0.0)


class TestRegressions(unittest.TestCase):
    def test_only_ok_to_not_ok_counts(self):
        base = {"a": {"ok": True}, "b": {"ok": False}, "c": {"ok": True}}
        cur = {"a": {"ok": False}, "b": {"ok": False}, "c": {"ok": True},
               "d": {"ok": False}}
        self.assertEqual(eval_report.regressions(base, cur), ["a"])

    def test_new_case_ignored(self):
        self.assertEqual(eval_report.regressions({}, {"new": {"ok": False}}), [])


class TestMarkdown(unittest.TestCase):
    def test_contains_dimensions_and_failures(self):
        summary = eval_report.dimension_summary(
            {"amount": 1, "direction": 1, "category": 1, "customer": 0, "ok": 0},
            1, {"金额": {"n": 1, "ok": 0}})
        failures = [{"text": "王阿姨 6 块",
                     "expected": {"amount": 6, "direction": "income",
                                  "category": "主营业务收入", "customer": "王阿姨"},
                     "got": {"amount": None, "trans_type": "income",
                             "category": "主营业务收入", "customer": "王阿姨"}}]
        md = eval_report.to_markdown({"model": "deepseek-v4.1-flash", "at": "2026-09-22"},
                                     summary, failures)
        self.assertIn("分维度准确率", md)
        self.assertIn("deepseek-v4.1-flash", md)
        self.assertIn("王阿姨 6 块", md)
        self.assertIn("失败用例（1）", md)

    def test_no_failures_branch(self):
        summary = eval_report.dimension_summary(
            {"amount": 2, "direction": 2, "category": 2, "customer": 2, "ok": 2}, 2, {})
        md = eval_report.to_markdown({"model": "m"}, summary, [])
        self.assertIn("全部通过", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
